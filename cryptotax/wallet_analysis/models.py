import uuid

from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator

User = get_user_model()

# Create your models here.
class WalletAnalysisOrder(models.Model):
    """
    Represents a user's order for a wallet analysis.
    Tracks the payment and the processing status.
    """
    # Status choices
    STATUS_PENDING_PAYMENT = 'pending_payment'
    STATUS_PAYMENT_RECEIVED = 'payment_received'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_PARTIAL_COMPLETE = 'partial_complete'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING_PAYMENT, 'Pending Payment'),
        (STATUS_PAYMENT_RECEIVED, 'Payment Received'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_PARTIAL_COMPLETE, 'Partially Complete'),
        (STATUS_FAILED, 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='wallet_analysis_orders',
        help_text='User who created this order'
    )

    wallet_address = models.CharField(
        max_length=42,
        help_text="EVM wallet address (0x...)"
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING_PAYMENT,
        db_index=True
    )

    payment_amount_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=50.00,
        validators=[MinValueValidator(50)]
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Wallet Analysis Order'
        verbose_name_plural = 'Wallet Analysis Orders'
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['user', 'status'])
        ]

    def __str__(self):
        return f"Order {self.id} - {self.wallet_address[:10]}... ({self.get_status_display()})"
    
    @property
    def is_paid(self):
        """Check if payment has been received"""
        return self.status == self.STATUS_PAYMENT_RECEIVED

    @property
    def is_complete(self):
        """Check if order is fully completed"""
        return self.status == self.STATUS_COMPLETED


class SolanaPayment(models.Model):
    """
    Tracks Solana Pay payment for a wallet analysis order.
    Stores payment details, reference ID, and transaction signature.
    """

    # Payment status choices
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_FINALIZED = 'finalized'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_FINALIZED, 'Finalized'),
        (STATUS_FAILED, 'Failed'),
    ]

    # Token choices
    TOKEN_USDC = 'USDC'
    TOKEN_USDT = 'USDT'

    TOKEN_CHOICES = [
        (TOKEN_USDC, 'USDC'),
        (TOKEN_USDT, 'USDT'),
    ]

    # Token mint addresses
    USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
    USDT_MINT = 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Link to order (one-to-one relationship)
    order = models.OneToOneField(
        WalletAnalysisOrder,
        on_delete=models.CASCADE,
        related_name='solana_payment',
        help_text='Wallet analysis order for this payment'
    )

    # Solana Pay URL and tracking
    payment_url = models.TextField(
        help_text='Full Solana Pay URL for payment'
    )

    reference = models.CharField(
        max_length=44,
        unique=True,
        help_text='Unique reference public key for tracking this payment on-chain (base58 encoded Solana public key)'
    )

    # Payment details
    recipient_address = models.CharField(
        max_length=44,
        help_text='Solana wallet address to receive payment'
    )

    amount_expected = models.BigIntegerField(
        help_text='Expected amount in lamports (smallest unit)'
    )

    token_type = models.CharField(
        max_length=4,
        choices=TOKEN_CHOICES,
        default=TOKEN_USDC,
        help_text='SPL token type (USDC or USDT)'
    )

    token_mint = models.CharField(
        max_length=44,
        help_text='SPL token mint address'
    )

    # Transaction details (filled after payment)
    transaction_signature = models.CharField(
        max_length=88,
        blank=True,
        null=True,
        help_text='Solana transaction signature (once paid)'
    )

    # Status and timestamps
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When payment was confirmed on blockchain'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Solana Payment'
        verbose_name_plural = 'Solana Payments'
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['reference']),  # For blockchain lookups
        ]

    def __str__(self):
        return f"Payment for Order {self.order.id} - {self.get_status_display()}"

    @property
    def is_paid(self):
        """Check if payment is confirmed or finalized"""
        return self.status in [self.STATUS_CONFIRMED, self.STATUS_FINALIZED]

    def get_token_mint_address(self):
        """Get the correct mint address based on token type"""
        return self.USDC_MINT if self.token_type == self.TOKEN_USDC else self.USDT_MINT


class DuneQueryJob(models.Model):
    """
    Tracks individual Dune query executions for an order.
    Allows partial success and manual retry for failed queries.
    """

    # Status choices
    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_FAILED_NEEDS_REVIEW = 'failed_needs_review'

    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_FAILED_NEEDS_REVIEW, 'Failed - Needs Review'),
    ]

    # Error type choices
    ERROR_QUERY = 'query_error'
    ERROR_NETWORK = 'network_error'
    ERROR_RATE_LIMIT = 'rate_limit'
    ERROR_SERVICE_OUTAGE = 'service_outage'
    ERROR_AUTH = 'auth_error'

    ERROR_TYPE_CHOICES = [
        (ERROR_QUERY, 'Query Error'),
        (ERROR_NETWORK, 'Network Error'),
        (ERROR_RATE_LIMIT, 'Rate Limit'),
        (ERROR_SERVICE_OUTAGE, 'Service Outage'),
        (ERROR_AUTH, 'Authentication Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Link to order
    order = models.ForeignKey(
        WalletAnalysisOrder,
        on_delete=models.CASCADE,
        related_name='dune_query_jobs',
        help_text='Order this query belongs to'
    )

    # Query identification
    query_name = models.CharField(
        max_length=100,
        help_text='Human-readable query name (e.g., "defi_trades", "lp_events")'
    )

    dune_query_id = models.IntegerField(
        help_text='Dune query ID from Dune Analytics'
    )

    dune_execution_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text='Execution ID returned by Dune API'
    )

    # Status and error tracking
    status = models.CharField(
        max_length=25,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True
    )

    error_message = models.TextField(
        blank=True,
        null=True,
        help_text='Error message if query failed'
    )

    error_type = models.CharField(
        max_length=20,
        choices=ERROR_TYPE_CHOICES,
        blank=True,
        null=True,
        help_text='Categorized error type for retry decision'
    )

    retry_count = models.IntegerField(
        default=0,
        help_text='Number of manual retry attempts'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When query execution started'
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When query execution completed'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Dune Query Job'
        verbose_name_plural = 'Dune Query Jobs'
        indexes = [
            models.Index(fields=['order', 'status']),
            models.Index(fields=['status', '-created_at']),
        ]
        # Prevent duplicate queries for same order
        unique_together = [['order', 'query_name']]

    def __str__(self):
        return f"{self.query_name} for Order {self.order.id} - {self.get_status_display()}"

    @property
    def is_complete(self):
        """Check if query completed successfully"""
        return self.status == self.STATUS_COMPLETED

    @property
    def is_retryable(self):
        """Check if this error type can be retried"""
        # Never retry query errors or auth errors
        non_retryable = [self.ERROR_QUERY, self.ERROR_AUTH]
        return self.error_type not in non_retryable if self.error_type else False

    @property
    def duration(self):
        """Calculate query execution duration"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class ReportFile(models.Model):
    """
    Represents a generated CSV report file for an order.
    Links to the actual file on disk.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Link to order
    order = models.ForeignKey(
        WalletAnalysisOrder,
        on_delete=models.CASCADE,
        related_name='report_files',
        help_text='Order this report belongs to'
    )

    # File details
    file_name = models.CharField(
        max_length=255,
        help_text='Original filename'
    )

    file_path = models.CharField(
        max_length=500,
        help_text='Relative path from MEDIA_ROOT'
    )

    file_type = models.CharField(
        max_length=50,
        help_text='Report type (e.g., "defi_trades", "lp_events")'
    )

    file_size = models.BigIntegerField(
        help_text='File size in bytes'
    )

    # Timestamp
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Report File'
        verbose_name_plural = 'Report Files'
        indexes = [
            models.Index(fields=['order', '-created_at']),
        ]

    def __str__(self):
        return f"{self.file_type} - {self.file_name}"

    @property
    def file_size_mb(self):
        """Return file size in megabytes"""
        return round(self.file_size / (1024 * 1024), 2)

    def get_absolute_path(self):
        """Get full filesystem path to file"""
        from django.conf import settings
        return settings.MEDIA_ROOT / self.file_path
