from datetime import datetime

from django.conf import settings
from dune_client.client import DuneClient
from dune_client.query import QueryBase
from dune_client.types import QueryParameter

def get_solana_token_transfers(wallet, start: datetime, end: datetime):
    dune = DuneClient(api_key=settings.DUNE_API_KEY)

    param_list = [
        QueryParameter.text_type(name="wallet", value=wallet),
        QueryParameter.text_type(name="starttime", value=start.strftime("%Y-%m-%d %H:%M:%S")),
        QueryParameter.text_type(name="endtime", value=end.strftime("%Y-%m-%d %H:%M:%S")),
    ]

    query = QueryBase(query_id=6022882, params=param_list)

    results = dune.run_query(query, ping_frequency=5)
    csv_data = dune.get_execution_results_csv(results.execution_id)
    return csv_data.data
