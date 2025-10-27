"""
Views for wallet analysis order creation and payment processing.
"""

import os
import json
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django_q.tasks import async_task

from .models import WalletAnalysisOrder, SolanaPayment
from .solana_utils import generate_solana_pay_url, verify_transaction_on_chain

import httpx


def validate_evm_address(address: str) -> bool:
    """
    Validate EVM wallet address format.

    Args:
        address: Address to validate

    Returns:
        True if valid EVM address, False otherwise
    """
    # EVM address: 0x followed by 40 hexadecimal characters
    pattern = r'^0x[a-fA-F0-9]{40}$'
    return bool(re.match(pattern, address))


def validate_sol_address(address: str) -> bool:
    pattern = r'^[1-9A-HJ-NP-Za-km-z]{32,44}$'
    return bool(re.match(pattern, address))


@login_required
@require_http_methods(["GET", "POST"])
def create_order_view(request):
    """
    Create a new wallet analysis order.

    GET: Display form to enter wallet address
    POST: Create order and redirect to payment page
    """
    if request.method == 'POST':
        wallet_address = request.POST.get('wallet_address', '').strip()

        # Validate wallet address
        if not wallet_address:
            return render(request, 'wallet_analysis/create_order.html', {
                'error': 'Please enter a wallet address'
            })

        if not validate_sol_address(wallet_address):
            return render(request, 'wallet_analysis/create_order.html', {
                'error': 'Invalid SVM wallet address.',
                'wallet_address': wallet_address
            })

        # Create order
        order = WalletAnalysisOrder.objects.create(
            user=request.user,
            wallet_address=wallet_address,
            status=WalletAnalysisOrder.STATUS_PENDING_PAYMENT
        )

        # Generate Solana Pay URL
        recipient = os.getenv('SOLANA_RECIPIENT_ADDRESS')

        if not recipient:
            return render(request, 'wallet_analysis/create_order.html', {
                'error': 'Payment system not configured. Please contact support.'
            })

        payment_url, reference = generate_solana_pay_url(
            recipient=recipient,
            amount_usd=float(order.payment_amount_usd),
            token_type='USDC'
        )

        # Create payment record
        payment = SolanaPayment.objects.create(
            order=order,
            payment_url=payment_url,
            reference=reference,
            recipient_address=recipient,
            amount_expected=int(order.payment_amount_usd * 1_000_000),  # Convert to lamports
            token_type=SolanaPayment.TOKEN_USDC,
            token_mint=settings.USDC_MINT  # Use network-aware mint from settings
        )

        # Redirect to payment page with 'new' parameter to indicate fresh order
        from django.urls import reverse
        payment_url_path = reverse('wallet_analysis:payment_page', kwargs={'order_id': order.id})
        return redirect(f"{payment_url_path}?new=1")

    # GET request - show form
    return render(request, 'wallet_analysis/create_order.html')


@login_required
def payment_page_view(request, order_id):
    """
    Display payment page with Solana Pay QR code and wallet connection.

    Args:
        order_id: UUID of the order
    """
    # Fetch order and verify ownership
    order = get_object_or_404(WalletAnalysisOrder, id=order_id, user=request.user)

    # Get payment details
    try:
        payment = order.solana_payment
    except SolanaPayment.DoesNotExist:
        return render(request, 'wallet_analysis/error.html', {
            'error': 'Payment record not found for this order.'
        })

    # If already paid, redirect to dashboard
    if payment.is_paid:
        return redirect('wallet_analysis:order_detail', order_id=order.id)

    # Check if this is a newly created order (from create_order view)
    is_new_order = request.GET.get('new', '0') == '1'

    context = {
        'order': order,
        'payment': payment,
        'payment_amount': order.payment_amount_usd,
        'recipient': payment.recipient_address,
        'reference': str(payment.reference),
        'token_mint': payment.token_mint,
        'amount_lamports': payment.amount_expected,
        'rpc_url': settings.SOLANA_RPC_URL,
        'is_new_order': is_new_order,
    }

    return render(request, 'wallet_analysis/payment.html', context)


@require_POST
@csrf_exempt  # We'll verify via signature instead
def verify_payment_api(request):
    """
    API endpoint to verify a payment transaction.
    Called by frontend after user completes payment in wallet.

    POST body: {"order_id": "uuid", "signature": "tx_signature"}

    Returns:
        JSON: {"success": bool, "message": str, "order_status": str}
    """
    try:
        # Parse request body
        data = json.loads(request.body)
        order_id = data.get('order_id')
        signature = data.get('signature')

        if not order_id or not signature:
            return JsonResponse({
                'success': False,
                'message': 'Missing order_id or signature'
            }, status=400)

        # Fetch payment
        try:
            payment = SolanaPayment.objects.select_related('order').get(
                order__id=order_id
            )
        except SolanaPayment.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Payment not found'
            }, status=404)

        # Check if already paid
        if payment.is_paid:
            return JsonResponse({
                'success': True,
                'message': 'Payment already confirmed',
                'order_status': payment.order.status
            })

        # Verify transaction on blockchain with retry logic
        # Transaction might not be immediately available after confirmation
        is_valid = False
        max_retries = 10
        retry_delay = 2  # seconds

        for attempt in range(max_retries):
            is_valid = verify_transaction_on_chain(
                signature=signature,
                recipient=payment.recipient_address,
                expected_amount=payment.amount_expected,
                token_mint=payment.token_mint,
                reference=payment.reference
            )

            if is_valid:
                break

            # If not the last attempt, wait and retry
            if attempt < max_retries - 1:
                print(f"Verification attempt {attempt + 1} failed, retrying in {retry_delay}s...")
                import time
                time.sleep(retry_delay)

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
            async_task(
                'wallet_analysis.tasks.execute_wallet_analysis',
                order_id=str(order.id)
            )

            return JsonResponse({
                'success': True,
                'message': 'Payment verified successfully! Your reports are being generated.',
                'order_status': order.status
            })
        else:
            return JsonResponse({
                'success': False,
                'message': 'Payment verification failed. Transaction does not match expected parameters.'
            }, status=400)

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Server error: {str(e)}'
        }, status=500)


@require_POST
@csrf_exempt
def solana_rpc_proxy(request):
    """
    Proxy Solana JSON-RPC requests to the configured SOLANA_RPC_URL (e.g., Helius).

    This prevents exposing the API key to the browser and avoids Helius 403 referer issues.
    """
    try:
        rpc_url = settings.SOLANA_RPC_URL
        if not rpc_url:
            return JsonResponse({'error': 'RPC URL not configured'}, status=500)

        # Forward the JSON body as-is to the upstream RPC
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        with httpx.Client(timeout=15.0) as client:
            upstream = client.post(rpc_url, content=request.body, headers=headers)

        # Pass through upstream response
        return JsonResponse(
            data=upstream.json(),
            status=upstream.status_code,
            safe=False
        )
    except httpx.RequestError as e:
        return JsonResponse({'error': f'Upstream request failed: {e}'}, status=502)
    except ValueError:
        # If upstream returned non-JSON, pass raw text
        return JsonResponse({'error': 'Invalid response from RPC'}, status=502)


@login_required
def payment_status_api(request, order_id):
    """
    API endpoint to check payment status.
    Used for polling if immediate verification fails.

    Args:
        order_id: UUID of the order

    Returns:
        JSON: {"payment_status": str, "order_status": str, "confirmed_at": str}
    """
    try:
        # Fetch order and verify ownership
        order = get_object_or_404(WalletAnalysisOrder, id=order_id, user=request.user)

        try:
            payment = order.solana_payment
        except SolanaPayment.DoesNotExist:
            return JsonResponse({
                'error': 'Payment not found'
            }, status=404)

        response_data = {
            'payment_status': payment.status,
            'order_status': order.status,
            'confirmed_at': payment.confirmed_at.isoformat() if payment.confirmed_at else None,
            'transaction_signature': payment.transaction_signature or None
        }

        return JsonResponse(response_data)

    except Exception as e:
        return JsonResponse({
            'error': str(e)
        }, status=500)

@login_required
@require_http_methods(["GET", "POST"])
def verify_signature_view(request, order_id):
    """
    Manual verification page to check a submitted transaction signature against
    the expected payment parameters for an order. On success, updates DB and
    redirects to the order detail (PRG). On failure, re-renders with error.
    """

    # Ensure the order belongs to the current user
    order = get_object_or_404(WalletAnalysisOrder, id=order_id, user=request.user)

    # Get payment details for this order
    try:
        payment = order.solana_payment
    except SolanaPayment.DoesNotExist:
        return render(request, 'wallet_analysis/verify_signature.html', {
            'order': order,
            'payment': None,
            'error': 'No payment record found for this order.'
        })

    if request.method == 'POST':
        # Accept either 'signature' or legacy 'tx_signature' field
        signature = (request.POST.get('signature') or request.POST.get('tx_signature') or '').strip()

        if not signature:
            return render(request, 'wallet_analysis/verify_signature.html', {
                'order': order,
                'payment': payment,
                'submitted_signature': '',
                'error': 'Please enter a transaction signature.'
            })

        # Verify on-chain (no retry here; this page is the manual fallback)
        is_valid = verify_transaction_on_chain(
            signature=signature,
            recipient=payment.recipient_address,
            expected_amount=payment.amount_expected,
            token_mint=payment.token_mint,
            reference=payment.reference
        )

        if is_valid:
            # Update payment status and order
            payment.transaction_signature = signature
            payment.status = SolanaPayment.STATUS_CONFIRMED
            payment.confirmed_at = timezone.now()
            payment.save()

            order.status = WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED
            order.save()

            # Kick off analysis tasks (same behavior as API verification)
            async_task('wallet_analysis.tasks.execute_wallet_analysis', order_id=str(order.id))

            from django.contrib import messages
            messages.success(request, 'Payment verified successfully. Your reports are being generated.')
            return redirect('wallet_analysis:order_detail', order_id=order.id)

        # Failed verification: show error and keep the form
        from django.contrib import messages
        messages.error(request, 'Verification failed. Signature does not match expected parameters.')
        return render(request, 'wallet_analysis/verify_signature.html', {
            'order': order,
            'payment': payment,
            'submitted_signature': signature,
            'error': 'Verification failed. Please double-check the signature.'
        })

    # GET
    return render(request, 'wallet_analysis/verify_signature.html', {
        'order': order,
        'payment': payment
    })


        

    
@login_required
def order_detail_view(request, order_id):
    """
    Display order details and available reports.

    Args:
        order_id: UUID of the order
    """
    # Fetch order and verify ownership
    order = get_object_or_404(WalletAnalysisOrder, id=order_id, user=request.user)

    # Get payment details
    try:
        payment = order.solana_payment
    except SolanaPayment.DoesNotExist:
        payment = None

    # Get query jobs
    query_jobs = order.dune_query_jobs.all()

    # Get report files
    report_files = order.report_files.all()

    context = {
        'order': order,
        'payment': payment,
        'query_jobs': query_jobs,
        'report_files': report_files,
    }

    return render(request, 'wallet_analysis/order_detail.html', context)


@login_required
def dashboard_view(request):
    """
    Display user's dashboard with all their wallet analysis orders.

    Shows order status, wallet addresses, and quick actions.
    """
    # Get all orders for the current user
    orders = WalletAnalysisOrder.objects.filter(
        user=request.user
    ).select_related('solana_payment').prefetch_related('report_files').order_by('-created_at')

    context = {
        'orders': orders,
    }

    return render(request, 'wallet_analysis/dashboard.html', context)


@login_required
def download_report_view(request, report_id):
    """
    Serve a report file for download.

    Verifies that the user owns the order before allowing download.

    Args:
        report_id: UUID of the report file
    """
    from django.http import FileResponse, Http404
    from .models import ReportFile

    # Get report and verify ownership
    try:
        report = ReportFile.objects.select_related('order').get(id=report_id)
    except ReportFile.DoesNotExist:
        raise Http404("Report not found")

    # Verify user owns this report's order
    if report.order.user != request.user:
        raise Http404("Report not found")

    # Get file path
    file_path = report.get_absolute_path()

    # Check if file exists
    if not file_path.exists():
        raise Http404("Report file not found on disk")

    # Serve file
    response = FileResponse(
        open(file_path, 'rb'),
        as_attachment=True,
        filename=report.file_name
    )

    return response

