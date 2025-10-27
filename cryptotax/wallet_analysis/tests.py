"""
Comprehensive tests for wallet_analysis app.
"""
import pytest
import responses
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils import timezone

from wallet_analysis.models import (
    WalletAnalysisOrder,
    SolanaPayment,
    DuneQueryJob,
    ReportFile
)
from wallet_analysis.solana_utils import (
    generate_solana_pay_url,
    get_solana_rpc_client,
    verify_transaction_on_chain,
    search_transactions_by_reference,
    get_references_from_signature,
)
from wallet_analysis.factories import (
    UserFactory,
    WalletAnalysisOrderFactory,
    SolanaPaymentFactory,
    DuneQueryJobFactory,
    ReportFileFactory
)

User = get_user_model()


# ============================================================================
# MODEL TESTS
# ============================================================================

@pytest.mark.django_db
class TestWalletAnalysisOrderModel:
    """Tests for WalletAnalysisOrder model."""

    def test_create_order(self):
        """Test creating a wallet analysis order."""
        order = WalletAnalysisOrderFactory()
        assert order.id is not None
        assert order.status == WalletAnalysisOrder.STATUS_PENDING_PAYMENT
        assert order.payment_amount_usd == Decimal('50.00')

    def test_order_str_representation(self):
        """Test string representation of order."""
        order = WalletAnalysisOrderFactory(
            wallet_address='0x1234567890abcdef1234567890abcdef12345678'
        )
        str_repr = str(order)
        assert '0x12345678' in str_repr
        assert 'Pending Payment' in str_repr

    def test_is_paid_property(self):
        """Test is_paid property."""
        order = WalletAnalysisOrderFactory(
            status=WalletAnalysisOrder.STATUS_PENDING_PAYMENT
        )
        assert not order.is_paid

        order.status = WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED
        assert order.is_paid

    def test_is_complete_property(self):
        """Test is_complete property."""
        order = WalletAnalysisOrderFactory(
            status=WalletAnalysisOrder.STATUS_PROCESSING
        )
        assert not order.is_complete

        order.status = WalletAnalysisOrder.STATUS_COMPLETED
        assert order.is_complete


@pytest.mark.django_db
class TestSolanaPaymentModel:
    """Tests for SolanaPayment model."""

    def test_create_payment(self):
        """Test creating a Solana payment."""
        payment = SolanaPaymentFactory()
        assert payment.id is not None
        assert payment.status == SolanaPayment.STATUS_PENDING
        assert payment.token_type == SolanaPayment.TOKEN_USDC

    def test_payment_order_relationship(self):
        """Test one-to-one relationship between payment and order."""
        payment = SolanaPaymentFactory()
        assert payment.order.solana_payment == payment

    def test_unique_reference(self):
        """Test that reference must be unique."""
        reference = 'test_reference_123'
        SolanaPaymentFactory(reference=reference)

        with pytest.raises(Exception):  # IntegrityError
            SolanaPaymentFactory(reference=reference)


@pytest.mark.django_db
class TestDuneQueryJobModel:
    """Tests for DuneQueryJob model."""

    def test_create_dune_job(self):
        """Test creating a Dune query job."""
        job = DuneQueryJobFactory()
        assert job.id is not None
        assert job.status == DuneQueryJob.STATUS_QUEUED
        assert job.retry_count == 0

    def test_dune_job_order_relationship(self):
        """Test relationship between Dune job and order."""
        job = DuneQueryJobFactory()
        assert job.order is not None
        assert job in job.order.dune_query_jobs.all()


@pytest.mark.django_db
class TestReportFileModel:
    """Tests for ReportFile model."""

    def test_create_report_file(self):
        """Test creating a report file."""
        report = ReportFileFactory()
        assert report.id is not None
        assert report.file_type == 'defi_trades'
        assert report.file_size == 1024

    def test_file_size_mb_property(self):
        """Test file_size_mb property."""
        report = ReportFileFactory(file_size=2048000)  # 2 MB
        assert abs(report.file_size_mb - 2.0) < 0.1

    def test_get_absolute_path(self):
        """Test get_absolute_path method."""
        report = ReportFileFactory()
        absolute_path = report.get_absolute_path()
        assert str(settings.MEDIA_ROOT) in str(absolute_path)


# ============================================================================
# SOLANA UTILS TESTS
# ============================================================================

@pytest.mark.unit
class TestSolanaPayURLGeneration:
    """Tests for Solana Pay URL generation."""

    def test_generate_usdc_payment_url(self):
        """Test generating a USDC payment URL."""
        recipient = 'TestRecipient1234567890ABCDEFGHIJK'
        amount = 50.0
        url, reference = generate_solana_pay_url(recipient, amount, 'USDC')

        assert url.startswith(f'solana:{recipient}')
        assert 'amount=50' in url
        assert f'spl-token={settings.USDC_MINT}' in url
        assert f'reference={reference}' in url
        assert 'label=' in url

    def test_generate_usdt_payment_url(self):
        """Test generating a USDT payment URL."""
        recipient = 'TestRecipient1234567890ABCDEFGHIJK'
        amount = 25.0
        url, reference = generate_solana_pay_url(recipient, amount, 'USDT')

        assert url.startswith(f'solana:{recipient}')
        assert 'amount=25' in url
        assert f'spl-token={settings.USDT_MINT}' in url

    def test_unique_references(self):
        """Test that each URL gets a unique reference."""
        recipient = 'TestRecipient1234567890ABCDEFGHIJK'
        _, ref1 = generate_solana_pay_url(recipient, 50.0, 'USDC')
        _, ref2 = generate_solana_pay_url(recipient, 50.0, 'USDC')

        assert ref1 != ref2

    def test_network_aware_token_mints(self):
        """Test that correct token mints are used based on network setting."""
        recipient = 'TestRecipient1234567890ABCDEFGHIJK'
        url, _ = generate_solana_pay_url(recipient, 50.0, 'USDC')

        # Should use the mint from settings (which is network-aware)
        expected_mint = settings.USDC_MINT
        assert f'spl-token={expected_mint}' in url


@pytest.mark.unit
class TestSolanaRPCClient:
    """Tests for Solana RPC client."""

    def test_get_rpc_client(self):
        """Test getting RPC client with correct URL."""
        client = get_solana_rpc_client()
        assert client is not None
        # Client should use the URL from settings
        assert client._provider.endpoint_uri == settings.SOLANA_RPC_URL


@pytest.mark.unit
@pytest.mark.payment
class TestTransactionVerification:
    """Tests for on-chain transaction verification."""

    @patch('wallet_analysis.solana_utils.Signature')
    @patch('wallet_analysis.solana_utils.get_solana_rpc_client')
    def test_verify_valid_transaction(self, mock_get_client, mock_signature_class):
        """Test verifying a valid transaction."""
        # Mock the signature conversion
        mock_sig = MagicMock()
        mock_signature_class.from_string.return_value = mock_sig

        # Mock the RPC client response
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Create a mock successful transaction response
        mock_tx_response = MagicMock()
        mock_tx_response.value = MagicMock()
        mock_tx_response.value.meta = MagicMock()
        mock_tx_response.value.meta.err = None

        # Mock transaction structure
        mock_tx_response.value.transaction = MagicMock()
        mock_tx_response.value.transaction.transaction = MagicMock()
        mock_tx_response.value.transaction.transaction.message = MagicMock()

        # Set up account keys with recipient ATA and reference
        from solders.pubkey import Pubkey
        recipient_key = Pubkey.from_string('11111111111111111111111111111111')
        reference_key = Pubkey.from_string('11111111111111111111111111111112')
        token_program_key = Pubkey.from_string('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')
        mint_key = Pubkey.from_string(settings.USDC_MINT)
        associated_token_program_id = Pubkey.from_string('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL')
        # Derive expected ATA: seeds [b"ata", recipient, token_program_id, mint]
        ata_pubkey, _ = Pubkey.find_program_address(
            [b"ata", bytes(recipient_key), bytes(token_program_key), bytes(mint_key)],
            associated_token_program_id,
        )

        # Include ATA (not wallet) to validate ATA-based recipient check
        mock_tx_response.value.transaction.transaction.message.account_keys = [
            ata_pubkey,
            reference_key,
            token_program_key,
        ]

        # Mock instruction
        mock_instruction = MagicMock()
        mock_instruction.program_id_index = 2  # Token program
        mock_instruction.accounts = [0, 1]
        mock_tx_response.value.transaction.transaction.message.instructions = [mock_instruction]

        mock_client.get_transaction.return_value = mock_tx_response

        # Test verification
        result = verify_transaction_on_chain(
            signature='test_signature',
            recipient=str(recipient_key),
            expected_amount=50_000_000,
            token_mint=settings.USDC_MINT,
            reference=str(reference_key)
        )

        assert result is True

    @patch('wallet_analysis.solana_utils.get_solana_rpc_client')
    def test_verify_transaction_not_found(self, mock_get_client):
        """Test verifying a non-existent transaction."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock response with no transaction
        mock_tx_response = MagicMock()
        mock_tx_response.value = None
        mock_client.get_transaction.return_value = mock_tx_response

        result = verify_transaction_on_chain(
            signature='nonexistent_signature',
            recipient='11111111111111111111111111111111',
            expected_amount=50_000_000,
            token_mint=settings.USDC_MINT,
            reference='11111111111111111111111111111112'
        )

        assert result is False

    @patch('wallet_analysis.solana_utils.get_solana_rpc_client')
    def test_verify_failed_transaction(self, mock_get_client):
        """Test verifying a failed transaction."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock a failed transaction
        mock_tx_response = MagicMock()
        mock_tx_response.value = MagicMock()
        mock_tx_response.value.meta = MagicMock()
        mock_tx_response.value.meta.err = {'InstructionError': [0, 'Custom']}

        mock_client.get_transaction.return_value = mock_tx_response

        result = verify_transaction_on_chain(
            signature='failed_signature',
            recipient='11111111111111111111111111111111',
            expected_amount=50_000_000,
            token_mint=settings.USDC_MINT,
            reference='11111111111111111111111111111112'
        )

        assert result is False


@pytest.mark.unit
@pytest.mark.payment
class TestTransactionSearch:
    """Tests for searching transactions by reference."""

    @patch('wallet_analysis.solana_utils.get_solana_rpc_client')
    def test_search_finds_transaction(self, mock_get_client):
        """Test searching for transaction by reference."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock signature response
        mock_sig = MagicMock()
        mock_sig.signature = 'found_signature_123'
        mock_response = MagicMock()
        mock_response.value = [mock_sig]
        mock_client.get_signatures_for_address.return_value = mock_response

        result = search_transactions_by_reference(
            reference='11111111111111111111111111111112',
            recipient='11111111111111111111111111111111'
        )

        assert result == 'found_signature_123'

    @patch('wallet_analysis.solana_utils.get_solana_rpc_client')
    def test_search_no_transaction_found(self, mock_get_client):
        """Test searching when no transaction exists."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock empty response
        mock_response = MagicMock()
        mock_response.value = []
        mock_client.get_signatures_for_address.return_value = mock_response

        result = search_transactions_by_reference(
            reference='11111111111111111111111111111112',
            recipient='11111111111111111111111111111111'
        )

        assert result is None


@pytest.mark.unit
class TestExtractReferences:
    """Tests for extracting reference accounts from a transaction signature."""

    @patch('wallet_analysis.solana_utils.get_solana_rpc_client')
    @patch('wallet_analysis.solana_utils.Signature')
    def test_get_references_from_signature(self, mock_signature_class, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_sig = MagicMock()
        mock_signature_class.from_string.return_value = mock_sig

        # Build a mock transaction with a transferChecked instruction and one reference
        from solders.pubkey import Pubkey
        source = Pubkey.from_string('So11111111111111111111111111111111111111112')
        destination = Pubkey.from_string('De11111111111111111111111111111111111111112')
        authority = Pubkey.from_string('Au11111111111111111111111111111111111111112')
        reference = Pubkey.from_string('Re11111111111111111111111111111111111111112')
        mint = Pubkey.from_string('Mi11111111111111111111111111111111111111112')
        token_prog = Pubkey.from_string('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')

        mock_tx_response = MagicMock()
        mock_tx_response.value = MagicMock()
        mock_tx_response.value.transaction = MagicMock()
        mock_tx_response.value.transaction.transaction = MagicMock()
        mock_tx_response.value.transaction.transaction.message = MagicMock()

        # account_keys order aligned with instruction.accounts
        mock_tx_response.value.transaction.transaction.message.account_keys = [
            source, destination, authority, reference, mint, token_prog
        ]

        # instruction referencing all accounts including the extra reference
        mock_instruction = MagicMock()
        mock_instruction.program_id_index = 5
        mock_instruction.accounts = [0, 1, 2, 3, 4]
        mock_instruction.parsed = {
            'type': 'transferChecked',
            'info': {
                'source': str(source),
                'destination': str(destination),
                'authority': str(authority),
                'mint': str(mint),
                'tokenAmount': {'amount': '50000000'},
            }
        }
        mock_tx_response.value.transaction.transaction.message.instructions = [mock_instruction]

        mock_client.get_transaction.return_value = mock_tx_response

        refs = get_references_from_signature('any_signature')
        assert refs == [str(reference)]


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.payment
class TestPaymentFlow:
    """Integration tests for the complete payment flow."""

    def test_create_order_and_payment(self):
        """Test creating an order and associated payment."""
        user = UserFactory()
        order = WalletAnalysisOrderFactory(user=user)

        # Use a test recipient address if settings doesn't have one
        recipient = settings.SOLANA_RECIPIENT_ADDRESS or 'TestRecipient1234567890ABCDEFGHIJK'

        # Generate payment
        payment_url, reference = generate_solana_pay_url(
            recipient=recipient,
            amount_usd=float(order.payment_amount_usd),
            token_type='USDC'
        )

        payment = SolanaPayment.objects.create(
            order=order,
            payment_url=payment_url,
            reference=reference,
            recipient_address=recipient,
            amount_expected=50_000_000,
            token_type=SolanaPayment.TOKEN_USDC,
            status=SolanaPayment.STATUS_PENDING
        )

        assert payment.order == order
        assert order.solana_payment == payment
        assert payment.reference == reference

    def test_payment_status_transitions(self):
        """Test payment status transitions."""
        payment = SolanaPaymentFactory(status=SolanaPayment.STATUS_PENDING)
        order = payment.order

        # Simulate payment confirmation
        payment.status = SolanaPayment.STATUS_CONFIRMED
        payment.save()

        # Order status should be updated (would be done by view/task)
        order.status = WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED
        order.save()

        payment.refresh_from_db()
        order.refresh_from_db()

        assert payment.status == SolanaPayment.STATUS_CONFIRMED
        assert order.is_paid


# ============================================================================
# DUNE INTEGRATION TESTS
# ============================================================================

@pytest.mark.django_db
@pytest.mark.dune
@responses.activate
class TestDuneIntegration:
    """Tests for Dune Analytics integration."""

    def test_execute_wallet_analysis_success(self):
        """Test successful execution of wallet analysis with Dune queries."""
        from wallet_analysis.tasks import execute_wallet_analysis
        from unittest.mock import patch, MagicMock

        # Create order
        order = WalletAnalysisOrderFactory(status=WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED)

        # Mock Dune client
        with patch('wallet_analysis.tasks.DuneClient') as mock_dune_class:
            mock_dune = MagicMock()
            mock_dune_class.return_value = mock_dune

            # Mock query execution
            mock_result = MagicMock()
            mock_result.execution_id = 'test-exec-123'
            mock_dune.run_query.return_value = mock_result

            # Mock execution status (completed immediately)
            mock_status = MagicMock()
            mock_status.state = 'QUERY_STATE_COMPLETED'
            mock_dune.get_execution_status.return_value = mock_status

            # Mock CSV data
            mock_csv = MagicMock()
            mock_csv.data = 'wallet,amount\n0x123,100'
            mock_dune.get_execution_results_csv.return_value = mock_csv

            # Execute task
            execute_wallet_analysis(str(order.id))

            # Verify order was updated
            order.refresh_from_db()
            assert order.status == WalletAnalysisOrder.STATUS_COMPLETED

            # Verify query jobs were created
            jobs = DuneQueryJob.objects.filter(order=order)
            assert jobs.count() > 0

            # Verify all jobs completed
            for job in jobs:
                assert job.status == DuneQueryJob.STATUS_COMPLETED
                assert job.dune_execution_id == 'test-exec-123'

            # Verify report files were created
            reports = ReportFile.objects.filter(order=order)
            assert reports.count() == jobs.count()

    def test_execute_wallet_analysis_query_failure(self):
        """Test handling of Dune query failure."""
        from wallet_analysis.tasks import execute_wallet_analysis
        from unittest.mock import patch, MagicMock

        order = WalletAnalysisOrderFactory(status=WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED)

        with patch('wallet_analysis.tasks.DuneClient') as mock_dune_class:
            mock_dune = MagicMock()
            mock_dune_class.return_value = mock_dune

            # Mock query execution failure
            mock_dune.run_query.side_effect = Exception("Query execution failed")

            # Execute task (should handle error gracefully)
            with pytest.raises(Exception):
                execute_wallet_analysis(str(order.id))

            # Verify order was marked as failed
            order.refresh_from_db()
            assert order.status == WalletAnalysisOrder.STATUS_FAILED

    def test_execute_wallet_analysis_partial_success(self):
        """Test handling of partial success (some queries fail)."""
        from wallet_analysis.tasks import execute_wallet_analysis
        from unittest.mock import patch, MagicMock

        order = WalletAnalysisOrderFactory(status=WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED)

        with patch('wallet_analysis.tasks.DuneClient') as mock_dune_class:
            mock_dune = MagicMock()
            mock_dune_class.return_value = mock_dune

            # First call succeeds, second fails
            success_result = MagicMock()
            success_result.execution_id = 'exec-success'

            call_count = [0]
            def run_query_side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return success_result
                else:
                    raise Exception("Second query failed")

            mock_dune.run_query.side_effect = run_query_side_effect

            # Mock successful execution for first query
            mock_status = MagicMock()
            mock_status.state = 'QUERY_STATE_COMPLETED'
            mock_dune.get_execution_status.return_value = mock_status

            mock_csv = MagicMock()
            mock_csv.data = 'wallet,amount\n0x123,100'
            mock_dune.get_execution_results_csv.return_value = mock_csv

            # Execute task
            execute_wallet_analysis(str(order.id))

            # Verify order status is partial complete
            order.refresh_from_db()
            assert order.status == WalletAnalysisOrder.STATUS_PARTIAL_COMPLETE

            # Verify we have both completed and failed jobs
            jobs = DuneQueryJob.objects.filter(order=order)
            completed = jobs.filter(status=DuneQueryJob.STATUS_COMPLETED)
            failed = jobs.filter(status=DuneQueryJob.STATUS_FAILED)

            assert completed.count() > 0
            assert failed.count() > 0

    def test_dune_error_classification(self):
        """Test that Dune errors are properly classified."""
        from wallet_analysis.tasks import execute_wallet_analysis
        from unittest.mock import patch, MagicMock

        order = WalletAnalysisOrderFactory(status=WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED)

        test_cases = [
            ("rate limit exceeded", DuneQueryJob.ERROR_RATE_LIMIT),
            ("401 unauthorized", DuneQueryJob.ERROR_AUTH),
            ("authentication failed", DuneQueryJob.ERROR_AUTH),
            ("query execution failed", DuneQueryJob.ERROR_QUERY),
        ]

        for error_message, expected_error_type in test_cases:
            # Clear previous jobs
            DuneQueryJob.objects.filter(order=order).delete()
            order.status = WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED
            order.save()

            with patch('wallet_analysis.tasks.DuneClient') as mock_dune_class:
                mock_dune = MagicMock()
                mock_dune_class.return_value = mock_dune
                mock_dune.run_query.side_effect = Exception(error_message)

                # Execute task
                try:
                    execute_wallet_analysis(str(order.id))
                except:
                    pass

                # Verify error was classified correctly
                jobs = DuneQueryJob.objects.filter(order=order, status=DuneQueryJob.STATUS_FAILED)
                assert jobs.exists()
                assert jobs.first().error_type == expected_error_type


@pytest.mark.django_db
@pytest.mark.dune
class TestDuneQueryJobModel:
    """Additional tests for DuneQueryJob model functionality."""

    def test_job_retry_tracking(self):
        """Test that retry count is tracked properly."""
        job = DuneQueryJobFactory()
        assert job.retry_count == 0

        job.retry_count += 1
        job.save()

        job.refresh_from_db()
        assert job.retry_count == 1

    def test_job_timing(self):
        """Test that job timing is recorded."""
        job = DuneQueryJobFactory()
        assert job.started_at is None
        assert job.completed_at is None

        job.status = DuneQueryJob.STATUS_RUNNING
        job.started_at = timezone.now()
        job.save()

        job.status = DuneQueryJob.STATUS_COMPLETED
        job.completed_at = timezone.now()
        job.save()

        job.refresh_from_db()
        assert job.started_at is not None
        assert job.completed_at is not None
        assert job.completed_at >= job.started_at
