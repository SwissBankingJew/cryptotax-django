"""
Django Q2 background tasks for wallet analysis processing.
"""

from ast import arguments
from datetime import datetime
import os
import time
from pathlib import Path
from django.utils import timezone
from django.conf import settings
from django.core.mail import send_mail
from django_q.tasks import async_task
from dune_client.client import DuneClient
from dune_client.query import QueryBase
from dune_client.types import QueryParameter

from .models import AnalysisRun, SolanaPayment, WalletAnalysisOrder, DuneQueryJob, ReportFile, X402Query
from django.db import IntegrityError
from .solana_utils import search_transactions_by_reference, verify_transaction_on_chain

print("======NIGGERS========")
print("DUNE_API_KEY: ", settings.DUNE_API_KEY)

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
                    'cryptotax.wallet_analysis.tasks.execute_wallet_analysis',
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

    Process:
        1. Create DuneQueryJob records for each query type
        2. Execute Dune queries via API
        3. Poll for completion
        4. Download CSV results
        5. Save to media/reports/{user_id}/{order_id}/
        6. Create ReportFile records
        7. Update order status to completed
        8. Send email notification
    """
    order = None
    try:
        order = WalletAnalysisOrder.objects.get(id=order_id)

        # Update status to processing
        order.status = WalletAnalysisOrder.STATUS_PROCESSING
        order.save()

        print(f"Starting wallet analysis for order {order.id}")
        print(f"Wallet address: {order.wallet_address}")

        # Check if Dune API key is configured
        if not settings.DUNE_API_KEY:
            raise ValueError("DUNE_API_KEY not configured in settings")

        # Initialize Dune client
        dune = DuneClient(api_key=settings.DUNE_API_KEY)

        # Get query configurations from settings
        queries_config = {
            'defi_activity': 6022401,
            'token_transfers': 6022882,
        }

        # Step 1: Create fresh DuneQueryJob records (assumes admin rerun deletes old jobs)
        query_jobs = []
        for query_name, query_id in queries_config.items():
            if not query_id:
                continue
            try:
                job = DuneQueryJob.objects.create(
                    order=order,
                    query_name=query_name,
                    dune_query_id=int(query_id),
                    status=DuneQueryJob.STATUS_QUEUED,
                )
                print(f"Created DuneQueryJob: {query_name} (query_id={query_id})")
            except IntegrityError:
                # As a safety, if duplicates exist, remove and recreate
                DuneQueryJob.objects.filter(order=order, query_name=query_name).delete()
                job = DuneQueryJob.objects.create(
                    order=order,
                    query_name=query_name,
                    dune_query_id=int(query_id),
                    status=DuneQueryJob.STATUS_QUEUED,
                )
                print(f"Recreated DuneQueryJob after duplicate: {query_name} (query_id={query_id})")
            query_jobs.append(job)

        if not query_jobs:
            raise ValueError("No Dune queries configured. Please set DUNE_QUERY_* environment variables.")

        # Step 2 & 3: Execute queries and poll for completion
        for job in query_jobs:
            try:
                # Update job status to running
                job.status = DuneQueryJob.STATUS_RUNNING
                job.started_at = timezone.now()
                job.save()

                print(f"Executing Dune query: {job.query_name}")

                # Build params EXACTLY as Dune queries expect
                if job.dune_query_id == 6022882:
                    # token_transfers expects: wallet, startime, endtime
                    param_list = [
                        QueryParameter.text_type(name="wallet", value=order.wallet_address),
                        QueryParameter.text_type(name="startime", value=datetime(2025, 1, 1).strftime("%Y-%m-%d %H:%M:%S")),
                        QueryParameter.text_type(name="endtime", value=datetime(2025, 12, 31).strftime("%Y-%m-%d %H:%M:%S")),
                    ]
                else:
                    # defi_activity expects: wallet, after_time
                    param_list = [
                        QueryParameter.text_type(name="wallet", value=order.wallet_address),
                        QueryParameter.text_type(name="after_time", value=datetime(2024, 1, 1).strftime("%Y-%m-%d %H:%M:%S")),
                    ]

                # Log params for debugging
                try:
                    print(f"[DUNE] query_id={job.dune_query_id} name={job.query_name} params="
                          f"{[(p.name, getattr(p, 'value', None)) for p in param_list]}")
                except Exception as e:
                    print(f"[DUNE] EXCEPTION: {e}")

                query = QueryBase(query_id=job.dune_query_id, params=param_list)

                # Execute query with better error visibility
                try:
                    results = dune.run_query(query)
                except Exception as e:
                    print("[DUNE] --- Request failed ---")
                    print(f"[DUNE] Exception: {type(e).__name__}: {e}")
                    resp = getattr(e, 'response', None)
                    if resp is not None:
                        try:
                            body = (resp.text or '')[:2000]
                            print(f"[DUNE] HTTP {resp.status_code} Body:\n{body}")
                        except Exception:
                            pass
                    cause = getattr(e, '__cause__', None)
                    if cause is not None and getattr(cause, 'response', None) is not None:
                        try:
                            body = (cause.response.text or '')[:2000]
                            print(f"[DUNE] Cause HTTP {cause.response.status_code} Body:\n{body}")
                        except Exception:
                            pass
                    raise

                # Get execution ID
                execution_id = results.execution_id
                job.dune_execution_id = execution_id
                job.save()

                print(f"Query {job.query_name} started with execution_id: {execution_id}")

                # Poll for completion (with timeout)
                # Allow up to ~30 minutes for completion
                max_wait_time = 1800
                poll_interval = 10  # Check every 10 seconds
                elapsed_time = 0

                while elapsed_time < max_wait_time:
                    # Get execution status
                    status = dune.get_execution_status(execution_id)

                    if status.state == "QUERY_STATE_COMPLETED":
                        print(f"Query {job.query_name} completed successfully")
                        break
                    elif status.state in ["QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"]:
                        raise Exception(f"Query failed with state: {status.state}")

                    # Still running, wait and check again
                    time.sleep(poll_interval)
                    elapsed_time += poll_interval
                    print(f"Query {job.query_name} still running... ({elapsed_time}s elapsed)")

                if elapsed_time >= max_wait_time:
                    raise TimeoutError(f"Query {job.query_name} timed out after {max_wait_time} seconds")

                # Step 4: Download CSV results
                csv_data = dune.get_execution_results_csv(execution_id)

                # Step 5: Save to media/reports/{user_id}/{order_id}/
                reports_dir = Path(settings.MEDIA_ROOT) / 'reports' / str(order.user.id) / str(order.id)
                reports_dir.mkdir(parents=True, exist_ok=True)

                file_name = f"{job.query_name}.csv"
                file_path = reports_dir / file_name

                with open(file_path, 'w') as f:
                    f.write(csv_data.data)

                file_size = file_path.stat().st_size

                print(f"Saved report: {file_path} ({file_size} bytes)")

                # Step 6: Create ReportFile record
                report_file = ReportFile.objects.create(
                    order=order,
                    file_name=file_name,
                    file_path=f"reports/{order.user.id}/{order.id}/{file_name}",
                    file_type=job.query_name,
                    file_size=file_size
                )

                # Mark job as completed
                job.status = DuneQueryJob.STATUS_COMPLETED
                job.completed_at = timezone.now()
                job.save()

                print(f"Query job {job.query_name} completed successfully")

            except Exception as query_error:
                # Handle query-specific errors
                print(f"Error executing query {job.query_name}: {query_error}")

                # Classify error type
                error_message = str(query_error)
                if "rate limit" in error_message.lower():
                    job.error_type = DuneQueryJob.ERROR_RATE_LIMIT
                elif "auth" in error_message.lower() or "401" in error_message:
                    job.error_type = DuneQueryJob.ERROR_AUTH
                elif "timeout" in error_message.lower():
                    job.error_type = DuneQueryJob.ERROR_NETWORK
                elif "query" in error_message.lower() or "execution" in error_message.lower():
                    job.error_type = DuneQueryJob.ERROR_QUERY
                else:
                    job.error_type = DuneQueryJob.ERROR_NETWORK

                job.status = DuneQueryJob.STATUS_FAILED
                job.error_message = error_message[:500]  # Truncate to fit in DB
                job.completed_at = timezone.now()
                job.save()

                # If a query times out or fails, avoid starting more long queries in the same task
                # to reduce risk of worker timeout. Break out and let user retry.
                if isinstance(query_error, TimeoutError):
                    break

        # Step 7: Update order status based on query results
        completed_jobs = sum(1 for job in query_jobs if job.status == DuneQueryJob.STATUS_COMPLETED)
        failed_jobs = sum(1 for job in query_jobs if job.status == DuneQueryJob.STATUS_FAILED)

        if completed_jobs == len(query_jobs):
            # All queries succeeded
            order.status = WalletAnalysisOrder.STATUS_COMPLETED
            print(f"All queries completed successfully for order {order.id}")
        elif completed_jobs > 0:
            # Some queries succeeded
            order.status = WalletAnalysisOrder.STATUS_PARTIAL_COMPLETE
            print(f"Partial success: {completed_jobs}/{len(query_jobs)} queries completed for order {order.id}")
        else:
            # All queries failed
            order.status = WalletAnalysisOrder.STATUS_FAILED
            print(f"All queries failed for order {order.id}")

        order.save()

        # Step 8: Send email notification
        # send_completion_email(order, completed_jobs, failed_jobs)

        print(f"Completed wallet analysis for order {order.id}")

    except WalletAnalysisOrder.DoesNotExist:
        print(f"Order {order_id} not found")
        raise
    except Exception as e:
        print(f"Error executing wallet analysis for order {order_id}: {e}")

        # Update order status to failed
        if order:
            order.status = WalletAnalysisOrder.STATUS_FAILED
            order.save()

        raise  # Re-raise so Django Q2 marks task as failed


def solana_analysis(order_id):
    order = WalletAnalysisOrder.objects.get(id=order_id)

    # check if there are existing jobs?? why idk maybe good to know?! IDK
    existing_jobs = DuneQueryJob.objects.filter(order=order)
    
    if len(existing_jobs) > 0:
        print("why do jobs already exist wtf")
        raise Exception(f"Dune Query Jobs already exist for: {order_id}")

    # We query from 2024-01-01 to 2025-12-31, 6 month interval
    for start, end in [
            (datetime(2024, 1, 1), datetime(2024, 5, 31)),
            (datetime(2024, 6, 1), datetime(2024, 12, 31)),
            (datetime(2025, 1, 1), datetime(2025, 6, 1)),
            (datetime(2025, 6, 1), datetime(2025, 12, 31)),
    ]:
        solana_token_transfers_job = DuneQueryJob(
            order=order,
            arguments={
                "wallet": order.wallet_address,
                "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end.strftime("%Y-%m-%d %H:%M:%S")
            },
            query_name="solana_token_transfers",
            dune_query_id=6022882            
        )
        solana_token_transfers_job.save()
        async_task(
            'cryptotax.wallet_analysis.dune_analysis.get_solana_token_transfers_job',
            solana_token_transfers_job.id,
            order.wallet_address,
            start,
            end
        )




# def send_completion_email(order, completed_count, failed_count):
#     """
#     Send email notification when report generation is complete.

#     Args:
#         order: WalletAnalysisOrder instance
#         completed_count: Number of successfully completed reports
#         failed_count: Number of failed reports
#     """
#     try:
#         subject = f"Your Crypto Tax Reports are Ready - Order {order.id}"

#         if completed_count > 0 and failed_count == 0:
#             message = f"""
# Hello,

# Great news! Your crypto tax reports for wallet {order.wallet_address} are now ready.

# We've successfully generated {completed_count} report(s) for you:
# """
#             # List all completed reports
#             for report in order.report_files.all():
#                 message += f"\n  - {report.file_type.replace('_', ' ').title()} ({report.file_size_mb:.2f} MB)"

#             message += f"""

# You can download your reports here:
# {settings.ALLOWED_HOSTS[0]}/analysis/order/{order.id}/

# Thank you for using CryptoTax!
# """
#         elif completed_count > 0:
#             message = f"""
# Hello,

# Your crypto tax reports for wallet {order.wallet_address} have been partially generated.

# Successfully generated: {completed_count} report(s)
# Failed: {failed_count} report(s)

# You can download the available reports here:
# {settings.ALLOWED_HOSTS[0]}/analysis/order/{order.id}/

# Some reports failed to generate. Please contact support or try again.

# Thank you for using CryptoTax!
# """
#         else:
#             message = f"""
# Hello,

# Unfortunately, we encountered issues generating your crypto tax reports for wallet {order.wallet_address}.

# All {failed_count} report(s) failed to generate.

# Please contact support at support@cryptotax.example.com with your order ID: {order.id}

# We apologize for the inconvenience.

# Thank you for using CryptoTax!
# """

#         send_mail(
#             subject=subject,
#             message=message,
#             from_email='noreply@cryptotax.example.com',
#             recipient_list=[order.user.email],
#             fail_silently=True  # Don't fail the task if email fails
#         )

#         print(f"Sent completion email to {order.user.email}")

#     except Exception as e:
#         print(f"Error sending completion email: {e}")
        # Don't raise - email failure shouldn't fail the entire task




def run_analysis_x402(query_id):
    try:
        query = X402Query.objects.get(id=query_id)
    except X402Query.DoesNotExist:
        return "Query not found"

    time.sleep(10)

    query.result = "this,is,a,result,value,csv,"
    query.save()

    return f"Analysis completed for {query_id}"
