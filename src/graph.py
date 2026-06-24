import logging
from collections import defaultdict
from pydantic import BaseModel, Field
from typing import List, Optional, Union, Dict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, BaseMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langchain_core.tools import tool
from langchain_core.language_models import BaseChatModel
from langchain_community.document_loaders import YoutubeLoader
from langchain_tavily import TavilySearch


from .prompts import SEARCH_SYS_MESSAGE, SYNTHESIZE_SYS_MESSAGE

class UrlResult(BaseModel):
    url: str = Field(default=None)
    content: str = Field(default=None)

class OutletResults(BaseModel):
    web_links: Dict[str, List[UrlResult]] = Field(default=None)

class GlobalState(BaseModel):
    query: List[BaseMessage]
    search_results: Union[OutletResults, str] = Field(default=None) # tavily search results
    search_was_used: bool = Field(default=False)
    query_result: Optional[AIMessage] = Field(default=None)

tavily_search = TavilySearch(max_results=7)

@tool
def get_search_results(query: str) -> str:
    """Runs a web search given a user input requiring updated information or specifying to use internet."""
    return tavily_search.invoke(query)

def get_search_node(llm: BaseChatModel, sys_message: str, logger: logging.Logger, domains: List[str]):
    """
    Asks the LLM (with tool access) whether a web search is needed.
    - Tool called  → fetch results, store in state.search_results
    - Tool skipped → store the direct AIMessage answer in state.query_result
                     so the synthesize node can pass it through cheaply.
    """
    llm_with_tools = llm.bind_tools([get_search_results])
 
    def _parse_search_results(search_results: ToolMessage) -> OutletResults:
        _web_results: Dict[str, List[UrlResult]] = defaultdict(list)
        web_results = OutletResults()
        results = search_results['results']
        for result in results:
            url_result = UrlResult()
            url_result.url = result['url']
            url_result.content = f'Title: {result["title"]}\nContent: {result["content"]}'
            outlet_name = next((p for p in domains if p in url_result.url), None)
            if outlet_name:
                _web_results[outlet_name].append(url_result)
        web_results.web_links = _web_results
        return web_results

    def _node(state: GlobalState) -> dict:
        logger.info("Node 1: deciding if web search is needed")
        ai_msg: AIMessage = llm_with_tools.invoke(
            [SystemMessage(sys_message)] + state.query
        )
 
        if not ai_msg.tool_calls:
            logger.info("No tool call — model answered directly")
            return {
                "search_results": None,
                "search_was_used": False,
                "query_result": ai_msg,
            }

        # Execute every search call
        web_search_result: OutletResults = None
        for call in ai_msg.tool_calls:
            if call["name"] == "get_search_results":
                logger.info(f"Calling get_search_results with args: {call['args']}", )
                result = get_search_results.invoke(call["args"])
                logger.info(f"search results {result}")
                web_search_result = _parse_search_results(result)
 
        logger.info(f"Search results fetched: {web_search_result.model_dump_json()}")
 
        return {
            "search_results": web_search_result,
            "search_was_used": True,
            "query_result": None
        }
    return _node

def get_synthesize_node(llm: BaseChatModel, sys_message: str, logger: logging.Logger):
    """
    Produces the final answer.
    - If search was used --> build a rich prompt with the results and re-ask the LLM.
    - If search was NOT used --> query_result already holds a direct answer; just pass it through.
    """
    def _node(state: GlobalState) -> dict:
        # Short-circuit: no search was needed, answer already in state
        if not state.search_was_used and state.query_result is not None:
            logger.info("Node 2: passing through direct answer (no search)")
            return {} # state doesnt change
 
        logger.info("Node 2: synthesizing answer from search results")
 

        synthesis_messages = (
            [SystemMessage(sys_message)]
            + state.query
            + [AIMessage(content=state.search_results.model_dump_json())]   # inject results as assistant context
        )
 
        final: AIMessage = llm.invoke(synthesis_messages)
        logger.info("Node 2 — synthesis complete")
        return {"query_result": final}
 
    return _node


def get_graph(llm: BaseChatModel, logger: logging.Logger, domains: List[str]):
    tavily_search.include_domains = domains
    workflow = StateGraph(GlobalState)
 
    workflow.add_node("search_or_answer",get_search_node(llm=llm, sys_message=SEARCH_SYS_MESSAGE, logger=logger, domains=domains))
    workflow.add_node("synthesize",get_synthesize_node(llm=llm, sys_message=SYNTHESIZE_SYS_MESSAGE, logger=logger))
 
    workflow.add_edge(START, "search_or_answer")
    workflow.add_edge("search_or_answer", "synthesize") # always synthesize
    workflow.add_edge("synthesize", END)
 
    return workflow.compile()