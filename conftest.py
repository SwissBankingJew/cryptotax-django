"""
Pytest configuration and shared fixtures for the cryptotax project.
"""
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
import factory
from faker import Faker

# Set up faker
fake = Faker()

User = get_user_model()


@pytest.fixture
def api_client():
    """DRF API client for testing API endpoints."""
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def authenticated_user(db):
    """Create and return an authenticated user."""
    user = User.objects.create_user(
        email=fake.email(),
        password='testpass123'
    )
    return user


@pytest.fixture
def authenticated_client(authenticated_user, client):
    """Django test client with authenticated user."""
    client.force_login(authenticated_user)
    return client


@pytest.fixture(autouse=True)
def setup_test_environment(settings):
    """
    Automatically configure test environment settings.
    This fixture runs for every test.
    """
    # Use test database
    settings.DATABASES['default']['NAME'] = ':memory:'

    # Use console email backend for tests
    settings.EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

    # Use devnet for Solana tests by default
    settings.SOLANA_NETWORK = 'devnet'

    # Disable Django Q2 async during tests (run synchronously)
    settings.Q_CLUSTER['sync'] = True

    return settings


@pytest.fixture
def mock_solana_rpc(responses):
    """
    Mock Solana RPC responses for testing without hitting the blockchain.
    Uses the responses library to mock HTTP calls.
    """
    import json

    def add_transaction_response(signature, confirmed=True, valid=True):
        """Add a mock transaction response."""
        if valid and confirmed:
            response_data = {
                "jsonrpc": "2.0",
                "result": {
                    "slot": 12345678,
                    "blockTime": 1234567890,
                    "transaction": {
                        "message": {
                            "accountKeys": [
                                "sender_pubkey",
                                "recipient_pubkey",
                                "token_program",
                            ],
                            "instructions": [
                                {
                                    "programIdIndex": 2,
                                    "accounts": [0, 1],
                                    "data": "base58_encoded_data"
                                }
                            ]
                        }
                    },
                    "meta": {
                        "err": None,
                        "status": {"Ok": None}
                    }
                },
                "id": 1
            }
        elif not confirmed:
            response_data = {
                "jsonrpc": "2.0",
                "result": None,
                "id": 1
            }
        else:
            response_data = {
                "jsonrpc": "2.0",
                "result": {
                    "meta": {
                        "err": {"InstructionError": [0, "Custom"]},
                    }
                },
                "id": 1
            }

        responses.add(
            responses.POST,
            settings.SOLANA_RPC_URL,
            json=response_data,
            status=200
        )

    return {
        'add_transaction_response': add_transaction_response,
    }


@pytest.fixture
def mock_dune_api(responses):
    """
    Mock Dune API responses for testing.
    """
    import json

    def add_query_execution_response(execution_id="test-exec-123", state="QUERY_STATE_COMPLETED"):
        """Add mock response for query execution."""
        responses.add(
            responses.POST,
            "https://api.dune.com/api/v1/query/123/execute",
            json={"execution_id": execution_id, "state": state},
            status=200
        )

    def add_execution_status_response(execution_id="test-exec-123", state="QUERY_STATE_COMPLETED"):
        """Add mock response for execution status check."""
        responses.add(
            responses.GET,
            f"https://api.dune.com/api/v1/execution/{execution_id}/status",
            json={"execution_id": execution_id, "state": state},
            status=200
        )

    def add_execution_results_response(execution_id="test-exec-123", csv_data="wallet,amount\n0x123,100"):
        """Add mock response for downloading results."""
        responses.add(
            responses.GET,
            f"https://api.dune.com/api/v1/execution/{execution_id}/results/csv",
            body=csv_data,
            status=200,
            content_type='text/csv'
        )

    return {
        'add_query_execution': add_query_execution_response,
        'add_execution_status': add_execution_status_response,
        'add_execution_results': add_execution_results_response,
    }
