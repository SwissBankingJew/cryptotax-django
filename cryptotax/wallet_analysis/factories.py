"""
Test factories for wallet_analysis models using factory_boy.
"""
import factory
from factory.django import DjangoModelFactory
from faker import Faker
from django.contrib.auth import get_user_model
from decimal import Decimal

from wallet_analysis.models import (
    WalletAnalysisOrder,
    SolanaPayment,
    DuneQueryJob,
    ReportFile
)

fake = Faker()
User = get_user_model()


class UserFactory(DjangoModelFactory):
    """Factory for creating test users."""
    class Meta:
        model = User
        django_get_or_create = ('email',)

    email = factory.LazyAttribute(lambda _: fake.email())
    password = factory.PostGenerationMethodCall('set_password', 'testpass123')


class WalletAnalysisOrderFactory(DjangoModelFactory):
    """Factory for creating wallet analysis orders."""
    class Meta:
        model = WalletAnalysisOrder

    user = factory.SubFactory(UserFactory)
    wallet_address = factory.LazyAttribute(lambda _: fake.hexify(text='0x' + '?' * 40))
    status = WalletAnalysisOrder.STATUS_PENDING_PAYMENT
    payment_amount_usd = Decimal('50.00')


class SolanaPaymentFactory(DjangoModelFactory):
    """Factory for creating Solana payments."""
    class Meta:
        model = SolanaPayment

    order = factory.SubFactory(WalletAnalysisOrderFactory)
    payment_url = factory.LazyAttribute(
        lambda obj: f"solana:{obj.recipient_address}?amount=50&reference={obj.reference}"
    )
    reference = factory.LazyAttribute(lambda _: fake.bothify(text='???????????????????????????????????????'))
    recipient_address = factory.LazyAttribute(lambda _: fake.bothify(text='???????????????????????????????????????'))
    amount_expected = 50_000_000  # 50 USDC in lamports (6 decimals)
    token_type = SolanaPayment.TOKEN_USDC
    status = SolanaPayment.STATUS_PENDING


class DuneQueryJobFactory(DjangoModelFactory):
    """Factory for creating Dune query jobs."""
    class Meta:
        model = DuneQueryJob

    order = factory.SubFactory(WalletAnalysisOrderFactory)
    query_name = 'defi_trades'
    dune_query_id = factory.LazyAttribute(lambda _: fake.random_int(min=100000, max=999999))
    status = DuneQueryJob.STATUS_QUEUED
    retry_count = 0


class ReportFileFactory(DjangoModelFactory):
    """Factory for creating report files."""
    class Meta:
        model = ReportFile

    order = factory.SubFactory(WalletAnalysisOrderFactory)
    file_name = factory.LazyAttribute(lambda obj: f"{obj.file_type}.csv")
    file_path = factory.LazyAttribute(
        lambda obj: f"reports/{obj.order.user.id}/{obj.order.id}/{obj.file_name}"
    )
    file_type = 'defi_trades'
    file_size = 1024  # 1 KB
