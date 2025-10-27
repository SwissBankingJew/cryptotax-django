"""
Django admin configuration for wallet_analysis app.
"""
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, path
from django.utils.safestring import mark_safe
from django_q.tasks import async_task
from django.utils import timezone

from .models import WalletAnalysisOrder, SolanaPayment, DuneQueryJob, ReportFile, X402Query

admin.site.register(X402Query)

@admin.register(WalletAnalysisOrder)
class WalletAnalysisOrderAdmin(admin.ModelAdmin):
    """Admin interface for WalletAnalysisOrder."""

    list_display = ['short_id', 'user_email', 'wallet_address_short', 'status', 'payment_amount_usd', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['id', 'wallet_address', 'user__email']
    readonly_fields = ['id', 'created_at', 'updated_at']
    date_hierarchy = 'created_at'
    actions = ['rerun_wallet_analysis']

    fieldsets = (
        ('Order Information', {
            'fields': ('id', 'user', 'wallet_address', 'status', 'payment_amount_usd')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def short_id(self, obj):
        """Display shortened order ID."""
        return str(obj.id)[:8]
    short_id.short_description = 'Order ID'

    def user_email(self, obj):
        """Display user email."""
        return obj.user.email
    user_email.short_description = 'User'
    user_email.admin_order_field = 'user__email'

    def wallet_address_short(self, obj):
        """Display shortened wallet address."""
        return f"{obj.wallet_address[:6]}...{obj.wallet_address[-4:]}"
    wallet_address_short.short_description = 'Wallet'

    @admin.action(description='Re-run wallet analysis for selected orders')
    def rerun_wallet_analysis(self, request, queryset):
        """Reset or create Dune jobs and requeue analysis for each order."""
        from django_q.tasks import async_task
        from .models import DuneQueryJob

        # Keep in sync with tasks.py current hardcoded mapping
        default_queries = {
            'defi_activity': 6022401,
            'token_transfers': 6022882,
        }

        requeued = 0
        for order in queryset:
            # MVP: remove old jobs to avoid UNIQUE constraint and create fresh ones
            DuneQueryJob.objects.filter(order=order).delete()

            # Ensure jobs exist and are reset to queued
            for name, qid in default_queries.items():
                DuneQueryJob.objects.create(
                    order=order,
                    query_name=name,
                    dune_query_id=int(qid),
                    status=DuneQueryJob.STATUS_QUEUED,
                )

            # Move order to processing state (optional visual cue)
            if order.status != order.STATUS_PROCESSING:
                order.status = order.STATUS_PROCESSING
                order.save()

            # Re-queue background task
            async_task('wallet_analysis.tasks.execute_wallet_analysis', order_id=str(order.id))
            requeued += 1

        self.message_user(request, f'Re-queued analysis for {requeued} order(s).')


@admin.register(SolanaPayment)
class SolanaPaymentAdmin(admin.ModelAdmin):
    """Admin interface for SolanaPayment."""

    list_display = ['short_id', 'order_link', 'status', 'token_type', 'amount_usd', 'transaction_link', 'created_at']
    list_filter = ['status', 'token_type', 'created_at']
    search_fields = ['id', 'order__id', 'order__wallet_address', 'transaction_signature', 'reference']
    readonly_fields = ['id', 'created_at', 'confirmed_at', 'payment_url_display', 'transaction_link_display']
    date_hierarchy = 'created_at'
    actions = ['manually_confirm_payment']
    change_list_template = 'admin/wallet_analysis/solanapayment/change_list.html'

    fieldsets = (
        ('Payment Information', {
            'fields': ('id', 'order', 'status', 'token_type', 'amount_expected')
        }),
        ('Solana Details', {
            'fields': ('recipient_address', 'reference', 'payment_url_display', 'transaction_signature', 'transaction_link_display')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'confirmed_at'),
        }),
    )

    def short_id(self, obj):
        """Display shortened payment ID."""
        return str(obj.id)[:8]
    short_id.short_description = 'Payment ID'

    def order_link(self, obj):
        """Link to related order."""
        url = reverse('admin:wallet_analysis_walletanalysisorder_change', args=[obj.order.id])
        return format_html('<a href="{}">{}</a>', url, str(obj.order.id)[:8])
    order_link.short_description = 'Order'

    def amount_usd(self, obj):
        """Display amount in USD."""
        return f"${obj.amount_expected / 1_000_000:.2f}"
    amount_usd.short_description = 'Amount'

    def transaction_link(self, obj):
        """Display transaction signature with link."""
        if obj.transaction_signature:
            return format_html(
                '<a href="https://solscan.io/tx/{}" target="_blank">{}...</a>',
                obj.transaction_signature,
                obj.transaction_signature[:8]
            )
        return '-'
    transaction_link.short_description = 'Transaction'

    def payment_url_display(self, obj): 
        """Display payment URL as copyable text."""
        return format_html('<textarea readonly style="width:100%;height:60px;">{}</textarea>', obj.payment_url)
    payment_url_display.short_description = 'Payment URL'

    def transaction_link_display(self, obj):
        """Display transaction link in admin."""
        if obj.transaction_signature:
            return format_html(
                '<a href="https://solscan.io/tx/{}" target="_blank">View on Solscan</a>',
                obj.transaction_signature
            )
        return 'No transaction yet'
    transaction_link_display.short_description = 'View Transaction'

    @admin.action(description='Manually confirm payment with transaction signature')
    def manually_confirm_payment(self, request, queryset):
        """
        Manually confirm selected payments by verifying transaction signatures.
        For customer service: when user paid but frontend didn't capture signature.
        """
        from django import forms
        from django.shortcuts import render
        from wallet_analysis.solana_utils import verify_transaction_on_chain

        # Only allow pending payments
        pending_payments = queryset.filter(status=SolanaPayment.STATUS_PENDING)

        if not pending_payments.exists():
            self.message_user(request, 'No pending payments selected.', level='warning')
            return

        if len(pending_payments) > 1:
            self.message_user(request, 'Please select only one payment at a time for manual confirmation.', level='warning')
            return

        payment = pending_payments.first()

        # Create a form for transaction signature input
        class TransactionSignatureForm(forms.Form):
            transaction_signature = forms.CharField(
                label='Transaction Signature',
                max_length=88,
                required=True,
                widget=forms.TextInput(attrs={
                    'size': 88,
                    'placeholder': 'Enter Solana transaction signature...'
                }),
                help_text='Paste the transaction signature from Solscan or user\'s wallet'
            )
            verify_on_chain = forms.BooleanField(
                label='Verify on blockchain',
                initial=True,
                required=False,
                help_text='Uncheck to skip on-chain verification (use with caution!)'
            )

        # If POST, process the form
        if 'apply' in request.POST:
            form = TransactionSignatureForm(request.POST)

            if form.is_valid():
                signature = form.cleaned_data['transaction_signature'].strip()
                verify = form.cleaned_data['verify_on_chain']

                # Update payment with signature
                payment.transaction_signature = signature

                if verify:
                    # Verify transaction on blockchain
                    try:
                        is_valid = verify_transaction_on_chain(
                            signature=signature,
                            recipient=payment.recipient_address,
                            expected_amount=payment.amount_expected,
                            token_mint=payment.token_mint,
                            reference=payment.reference
                        )

                        if not is_valid:
                            self.message_user(
                                request,
                                f'❌ Transaction verification FAILED for payment {str(payment.id)[:8]}. '
                                f'The transaction either does not exist, has wrong recipient/amount/token, '
                                f'or is missing the reference pubkey. Signature: {signature[:16]}... '
                                f'Double-check on Solscan and try again.',
                                level='error'
                            )
                            # Redirect back to changelist with error message
                            from django.shortcuts import redirect
                            return redirect('admin:wallet_analysis_solanapayment_changelist')
                    except Exception as e:
                        self.message_user(
                            request,
                            f'❌ Error verifying transaction on blockchain: {str(e)}. '
                            f'Check if the signature is valid and the RPC is accessible.',
                            level='error'
                        )
                        from django.shortcuts import redirect
                        return redirect('admin:wallet_analysis_solanapayment_changelist')

                # Mark payment as confirmed
                payment.status = SolanaPayment.STATUS_CONFIRMED
                payment.confirmed_at = timezone.now()
                payment.save()

                # Update order status
                order = payment.order
                order.status = WalletAnalysisOrder.STATUS_PAYMENT_RECEIVED
                order.save()

                # Queue Dune analysis
                async_task(
                    'wallet_analysis.tasks.execute_wallet_analysis',
                    order_id=str(order.id)
                )

                self.message_user(
                    request,
                    f'Payment confirmed! Order {str(order.id)[:8]} queued for analysis.',
                    level='success'
                )
                return
        else:
            form = TransactionSignatureForm()

        # Render confirmation page
        context = {
            'title': 'Manually Confirm Payment',
            'form': form,
            'payment': payment,
            'order': payment.order,
            'opts': self.model._meta,
            'action_name': 'manually_confirm_payment',
        }

        return render(request, 'admin/wallet_analysis/confirm_payment.html', context)

    # -----------------------------
    # Tools: Find by Signature
    # -----------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'find-by-signature/',
                self.admin_site.admin_view(self.find_by_signature_view),
                name='wallet_analysis_find_by_signature',
            ),
        ]
        return custom_urls + urls

    def find_by_signature_view(self, request):
        from django import forms
        from django.shortcuts import render
        from wallet_analysis.solana_utils import get_references_from_signature, decode_transaction_for_debug

        class FindBySignatureForm(forms.Form):
            signature = forms.CharField(
                label='Transaction Signature',
                max_length=88,
                required=True,
                widget=forms.TextInput(attrs={'size': 88, 'placeholder': 'Paste Solana transaction signature'}),
                help_text='Paste the signature from the user; we will extract the reference and locate the order.'
            )

        results = []
        refs = []
        sig_value = None
        debug = None
        if request.method == 'POST':
            form = FindBySignatureForm(request.POST)
            if form.is_valid():
                sig_value = form.cleaned_data['signature'].strip()
                # Get debug info and candidates
                debug = decode_transaction_for_debug(sig_value)
                refs = get_references_from_signature(sig_value)
                candidates = refs if refs else (debug.get('reference_candidates') if debug else [])
                if candidates:
                    qs = SolanaPayment.objects.select_related('order').filter(reference__in=candidates)
                    results = list(qs)
        else:
            form = FindBySignatureForm()

        context = {
            'title': 'Find Order by Transaction Signature',
            'form': form,
            'results': results,
            'refs': refs,
            'sig_value': sig_value,
            'opts': self.model._meta,
            'debug': debug,
        }
        return render(request, 'admin/wallet_analysis/find_by_signature.html', context)


@admin.register(DuneQueryJob)
class DuneQueryJobAdmin(admin.ModelAdmin):
    """Admin interface for DuneQueryJob with retry actions."""

    list_display = ['short_id', 'order_link', 'query_name', 'status', 'error_type', 'retry_count', 'execution_time', 'started_at']
    list_filter = ['status', 'error_type', 'query_name', 'started_at']
    search_fields = ['id', 'order__id', 'query_name', 'dune_query_id', 'dune_execution_id', 'error_message']
    readonly_fields = ['id', 'order', 'dune_execution_id', 'started_at', 'completed_at', 'execution_time_display']
    date_hierarchy = 'started_at'
    actions = ['retry_failed_queries', 'mark_needs_review']

    fieldsets = (
        ('Query Information', {
            'fields': ('id', 'order', 'query_name', 'dune_query_id', 'dune_execution_id')
        }),
        ('Status', {
            'fields': ('status', 'error_type', 'error_message', 'retry_count')
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at', 'execution_time_display'),
        }),
    )

    def short_id(self, obj):
        """Display shortened job ID."""
        return str(obj.id)[:8]
    short_id.short_description = 'Job ID'

    def order_link(self, obj):
        """Link to related order."""
        url = reverse('admin:wallet_analysis_walletanalysisorder_change', args=[obj.order.id])
        return format_html('<a href="{}">{}</a>', url, str(obj.order.id)[:8])
    order_link.short_description = 'Order'

    def execution_time(self, obj):
        """Display execution time."""
        if obj.started_at and obj.completed_at:
            delta = obj.completed_at - obj.started_at
            return f"{delta.total_seconds():.1f}s"
        return '-'
    execution_time.short_description = 'Time'

    def execution_time_display(self, obj):
        """Display execution time in admin detail."""
        if obj.started_at and obj.completed_at:
            delta = obj.completed_at - obj.started_at
            return f"{delta.total_seconds():.2f} seconds"
        return 'Not completed'
    execution_time_display.short_description = 'Execution Time'

    @admin.action(description='Retry selected failed queries')
    def retry_failed_queries(self, request, queryset):
        """Retry selected failed Dune queries."""
        # Filter only failed queries
        failed_jobs = queryset.filter(status__in=[DuneQueryJob.STATUS_FAILED, DuneQueryJob.STATUS_FAILED_NEEDS_REVIEW])

        if not failed_jobs.exists():
            self.message_user(request, 'No failed queries selected.', level='warning')
            return

        # MVP: delete all jobs for the impacted orders, create fresh on rerun
        order_ids = list(failed_jobs.values_list('order_id', flat=True).distinct())
        count = 0
        for order_id in order_ids:
            DuneQueryJob.objects.filter(order_id=order_id).delete()
            async_task('wallet_analysis.tasks.execute_wallet_analysis', order_id=str(order_id))
            count += 1

        self.message_user(
            request,
            f'Successfully queued {count} job(s) for retry.',
            level='success'
        )

    @admin.action(description='Mark as needs review')
    def mark_needs_review(self, request, queryset):
        """Mark failed queries as needing manual review."""
        failed_jobs = queryset.filter(status=DuneQueryJob.STATUS_FAILED)

        if not failed_jobs.exists():
            self.message_user(request, 'No failed queries selected.', level='warning')
            return

        count = failed_jobs.update(status=DuneQueryJob.STATUS_FAILED_NEEDS_REVIEW)

        self.message_user(
            request,
            f'Marked {count} job(s) as needing review.',
            level='success'
        )


@admin.register(ReportFile)
class ReportFileAdmin(admin.ModelAdmin):
    """Admin interface for ReportFile."""

    list_display = ['short_id', 'order_link', 'file_type', 'file_name', 'file_size_display', 'download_link', 'created_at']
    list_filter = ['file_type', 'created_at']
    search_fields = ['id', 'order__id', 'file_name', 'file_type']
    readonly_fields = ['id', 'created_at', 'file_size_display', 'download_link_display']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Report Information', {
            'fields': ('id', 'order', 'file_type', 'file_name')
        }),
        ('File Details', {
            'fields': ('file_path', 'file_size', 'file_size_display', 'download_link_display')
        }),
        ('Timestamp', {
            'fields': ('created_at',),
        }),
    )

    def short_id(self, obj):
        """Display shortened report ID."""
        return str(obj.id)[:8]
    short_id.short_description = 'Report ID'

    def order_link(self, obj):
        """Link to related order."""
        url = reverse('admin:wallet_analysis_walletanalysisorder_change', args=[obj.order.id])
        return format_html('<a href="{}">{}</a>', url, str(obj.order.id)[:8])
    order_link.short_description = 'Order'

    def file_size_display(self, obj):
        """Display file size in MB."""
        return f"{obj.file_size_mb:.2f} MB"
    file_size_display.short_description = 'File Size'

    def download_link(self, obj):
        """Display download link."""
        url = reverse('wallet_analysis:download_report', args=[obj.id])
        return format_html('<a href="{}">Download</a>', url)
    download_link.short_description = 'Download'

    def download_link_display(self, obj):
        """Display download link in admin detail."""
        url = reverse('wallet_analysis:download_report', args=[obj.id])
        return format_html('<a href="{}" target="_blank">Download Report</a>', url)
    download_link_display.short_description = 'Download Report'
