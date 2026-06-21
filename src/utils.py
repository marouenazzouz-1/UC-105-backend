import azure.functions as func
import json

def _error(status: int, message: str, detail: str = "") -> func.HttpResponse:
    body: dict = {"error": message}
    if detail:
        body["detail"] = detail
    return func.HttpResponse(
        json.dumps(body), status_code=status, mimetype="application/json"
    )