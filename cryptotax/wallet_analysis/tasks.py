"""
Django Q2 background tasks for wallet analysis processing.
"""

from django.utils import timezone
from .models import SolanaPayment, WalletAnalysisOrder
from .solana_utils import search_transactions_by_reference, verify_transaction_on_chain


def check_pending_payments():
    """
    Scheduled task to check for pending payments on the blockchain.
    Runs every 30 seconds via Django Q2.

    This is a fallback in case immediate frontend verification fails
    (user closes browser, network issues, etc.)
    """
    # Find all pending payments older than 2 minutes
    # (gives frontend time to verify first)
    two_minutes_ago = timezone.now() - timezone.timedelta(minutes=2)

    pending_payments = SolanaPayment.objects.filter(
        status=SolanaPayment.STATUS_PENDING,
        created_at__lt=two_minutes_ago
    ).select_related('order')

    verified_count = 0

    for payment in pending_payments:
        # Search blockchain for transactions with this reference
        signature = search_transactions_by_reference(
            reference=payment.reference,
            recipient=payment.recipient_address
        )

        if signature:
            # Found a transaction, now verify it
            is_valid = verify_transaction_on_chain(
                signature=signature,
                recipient=payment.recipient_address,
                expected_amount=payment.amount_expected,
                token_mint=payment.token_mint,
                reference=payment.reference
            )

            if is_valid:
                # Update payment status
                payment.transaction_signature = signature
                payment.status = SolanaPayment.STATUS_CONFIRMED
                payment.confirmed_at = timezone.now()
                payment.save()

                # Update order status
                order = payment.order
                order.status = WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED
                order.save()

                # Queue Dune query execution
                from django_q.tasks import async_task
                async_task(
                    'wallet_analysis.tasks.execute_wallet_analysis',
                    order_id=str(order.id)
                )

                verified_count += 1
                print(f"Background task verified payment for order {order.id}")

    if verified_count > 0:
        print(f"check_pending_payments: Verified {verified_count} payment(s)")

    return verified_count


def execute_wallet_analysis(order_id):
    """
    Execute Dune Analytics queries for a wallet analysis order.
    This is queued as an async task when payment is confirmed.

    Args:
        order_id: UUID string of the order

    This is a placeholder implementation. Full Dune integration will be added in Phase 4.
    """
    try:
        order = WalletAnalysisOrder.objects.get(id=order_id)

        # Update status to processing
        order.status = WalletAnalysisOrder.STATUS_PROCESSING
        order.save()

        print(f"Starting wallet analysis for order {order.id}")
        print(f"Wallet address: {order.wallet_address}")

        # TODO: Phase 4 - Dune Integration
        # 1. Create DuneQueryJob records for each query type
        # 2. Execute Dune queries via API
        # 3. Poll for completion
        # 4. Download CSV results
        # 5. Save to media/reports/{user_id}/{order_id}/
        # 6. Create ReportFile records
        # 7. Update order status to completed
        # 8. Send email notification

        # For now, just log that we would execute queries
        print(f"TODO: Execute Dune queries for wallet {order.wallet_address}")

        # Placeholder: Mark as completed after "processing"
        # Remove this when real Dune integration is added
        import time
        time.sleep(5)  # Simulate processing

        order.status = WalletAnalysisOrder.STATUS_COMPLETED
        order.save()

        print(f"Completed wallet analysis for order {order.id}")

    except WalletAnalysisOrder.DoesNotExist:
        print(f"Order {order_id} not found")
    except Exception as e:
        print(f"Error executing wallet analysis for order {order_id}: {e}")

        # Update order status to failed
        try:
            order = WalletAnalysisOrder.objects.get(id=order_id)
            order.status = WalletAnalysisOrder.STATUS_FAILED
            order.save()
        except Exception:
            pass

        raise  # Re-raise so Django Q2 marks task as failed
