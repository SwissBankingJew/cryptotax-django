"""
Django management command to create test payment orders for devnet testing.

Usage:
    python manage.py create_test_payment
    python manage.py create_test_payment --email user@example.com
    python manage.py create_test_payment --wallet 0x1234567890abcdef
    python manage.py create_test_payment --amount 50
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.conf import settings
from decimal import Decimal
import qrcode
from io import BytesIO
import sys

from wallet_analysis.models import WalletAnalysisOrder, SolanaPayment
from wallet_analysis.solana_utils import generate_solana_pay_url

User = get_user_model()


class Command(BaseCommand):
    help = 'Create a test payment order for devnet testing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            default='test@example.com',
            help='Email address for the test user (default: test@example.com)'
        )
        parser.add_argument(
            '--wallet',
            type=str,
            default='0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb',
            help='EVM wallet address to analyze (default: example address)'
        )
        parser.add_argument(
            '--amount',
            type=float,
            default=50.0,
            help='Payment amount in USD (default: 50.0)'
        )
        parser.add_argument(
            '--token',
            type=str,
            choices=['USDC', 'USDT'],
            default='USDC',
            help='Token type for payment (default: USDC)'
        )
        parser.add_argument(
            '--no-qr',
            action='store_true',
            help='Skip QR code generation'
        )

    def handle(self, *args, **options):
        email = options['email']
        wallet_address = options['wallet']
        amount = Decimal(str(options['amount']))
        token_type = options['token']
        show_qr = not options['no_qr']

        # Check if we're on devnet
        if settings.SOLANA_NETWORK != 'devnet':
            self.stdout.write(
                self.style.WARNING(
                    f"\nWARNING: Current network is '{settings.SOLANA_NETWORK}', not 'devnet'."
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    "Set SOLANA_NETWORK=devnet in your .env file for testing.\n"
                )
            )
            response = input("Continue anyway? (y/N): ")
            if response.lower() != 'y':
                self.stdout.write(self.style.ERROR('Aborted.'))
                return

        # Get or create test user
        user, created = User.objects.get_or_create(
            email=email,
            defaults={'password': 'testpass123'}
        )

        if created:
            user.set_password('testpass123')
            user.save()
            self.stdout.write(
                self.style.SUCCESS(f'\nCreated test user: {email} (password: testpass123)')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'\nUsing existing user: {email}')
            )

        # Create wallet analysis order
        order = WalletAnalysisOrder.objects.create(
            user=user,
            wallet_address=wallet_address,
            payment_amount_usd=amount,
            status=WalletAnalysisOrder.STATUS_PENDING_PAYMENT
        )

        self.stdout.write(
            self.style.SUCCESS(f'Created order: {order.id}')
        )

        # Generate Solana Pay URL
        recipient = settings.SOLANA_RECIPIENT_ADDRESS
        payment_url, reference = generate_solana_pay_url(
            recipient=recipient,
            amount_usd=float(amount),
            token_type=token_type
        )

        # Create Solana payment record
        payment = SolanaPayment.objects.create(
            order=order,
            payment_url=payment_url,
            reference=reference,
            recipient_address=recipient,
            amount_expected=int(amount * 1_000_000),  # Convert to lamports (6 decimals)
            token_type=token_type,
            status=SolanaPayment.STATUS_PENDING
        )

        self.stdout.write(
            self.style.SUCCESS(f'Created payment: {payment.id}\n')
        )

        # Display payment details
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        self.stdout.write(self.style.HTTP_INFO('PAYMENT DETAILS'))
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        self.stdout.write(f'Order ID:         {order.id}')
        self.stdout.write(f'Wallet Address:   {wallet_address}')
        self.stdout.write(f'Amount:           {amount} {token_type}')
        self.stdout.write(f'Network:          {settings.SOLANA_NETWORK}')
        self.stdout.write(f'Reference:        {reference}')
        self.stdout.write(f'Recipient:        {recipient}')
        self.stdout.write(self.style.HTTP_INFO('-' * 80))
        self.stdout.write(f'\nPayment URL:\n{payment_url}\n')
        self.stdout.write(self.style.HTTP_INFO('=' * 80))

        # Generate QR code if requested
        if show_qr:
            try:
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(payment_url)
                qr.make(fit=True)

                # Print QR code to terminal
                self.stdout.write('\nQR Code (scan with Solana wallet):\n')
                qr.print_ascii(invert=True)

            except ImportError:
                self.stdout.write(
                    self.style.WARNING(
                        '\nInstall qrcode package for QR code generation: pip install qrcode'
                    )
                )

        # Display testing instructions
        self.stdout.write(self.style.HTTP_INFO('\n' + '=' * 80))
        self.stdout.write(self.style.HTTP_INFO('TESTING INSTRUCTIONS'))
        self.stdout.write(self.style.HTTP_INFO('=' * 80))
        self.stdout.write('\n1. Make sure you have a Solana wallet with devnet tokens')
        self.stdout.write('   - Get devnet SOL: https://faucet.solana.com/')
        self.stdout.write(f'   - Get devnet {token_type}: Use SPL token faucet or swap')
        self.stdout.write('\n2. Access the payment page:')
        self.stdout.write(f'   http://localhost:8000/analysis/order/{order.id}/payment/')
        self.stdout.write('\n3. Or use the payment URL directly in your wallet app')
        self.stdout.write('\n4. After payment, check the order status:')
        self.stdout.write(f'   http://localhost:8000/analysis/order/{order.id}/')
        self.stdout.write('\n5. Monitor background task for payment detection:')
        self.stdout.write('   python manage.py qcluster')
        self.stdout.write(self.style.HTTP_INFO('\n' + '=' * 80 + '\n'))

        # Provide quick login credentials
        self.stdout.write(self.style.SUCCESS('Quick Login Credentials:'))
        self.stdout.write(f'Email:    {email}')
        self.stdout.write(f'Password: testpass123')
        self.stdout.write('')
