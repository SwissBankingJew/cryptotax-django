from datetime import timedelta
import os, sys


# Ensure Python can import the Django project and the app package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cryptotax.settings")

import django
django.setup()

from django_q.tasks import async_task
from fastapi import FastAPI, HTTPException, Query
from x402.fastapi.middleware import require_payment


from wallet_analysis.models import X402Query

app = FastAPI()
app.middleware("http")(
    require_payment(
        path="/solana/dextrades",
        price="0.01",
        pay_to_address="0x77e09eCBc3020432a791659E4240F1FB48706338",
        network="base-sepolia"
    )
)


@app.get("/solana/dextrades")
def root(
        wallet: str = Query(..., description="Solana wallet address")
):
    query = X402Query.objects.create(wallet=wallet)

    async_task("wallet_analysis.tasks.run_analysis_x402", str(query.id))

    eta = (query.created_at + timedelta(minutes=2)).isoformat()
    return {
        "completed": False,
        "query_id": str(query.id),
        "eta": eta
    }


@app.get("/results/solana/dextrades/{query_id}")
def check_status(query_id: str):
    try:
        query = X402Query.objects.get(id=query_id)
    except X402Query.DoesNotExist:
        raise HTTPException(status_code=404, detail="Query not found")

    result = query.result

    return {
        "completed": query.result is not None,
        "query_id": str(query.id),
        "result": result
    }
