from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cryptotax.settings")

import django
django.setup()

from django.conf import settings
from dune_client.client import DuneClient
from dune_client.query import QueryBase
from dune_client.types import QueryParameter

from wallet_analysis.models import DuneQueryJob

wallet = "Hd3Me1tLbRmmi7ujbM88ziJTgcN2zU9pafUSsGMirngY"
start = datetime(2025, 1, 1, 0, 0, 0)
end = datetime(2025, 12, 31, 0, 0, 0)

def get_solana_token_transfers(wallet, start: datetime, end: datetime):
    dune = DuneClient(api_key=settings.DUNE_API_KEY)

    param_list = [
        QueryParameter.text_type(name="wallet", value=wallet),
        QueryParameter.text_type(name="starttime", value=start.strftime("%Y-%m-%d %H:%M:%S")),
        QueryParameter.text_type(name="endtime", value=end.strftime("%Y-%m-%d %H:%M:%S")),
    ]

    query = QueryBase(query_id=6022882, params=param_list)

    results = dune.run_query(query, ping_frequency=30)
    csv_data = dune.get_execution_results_csv(results.execution_id)
    return csv_data.data.read().decode('utf-8')
