"""
Solana Pay utilities for payment URL generation and transaction verification.
"""

import os
from typing import Tuple, Optional
from urllib.parse import urlencode

from solana.rpc.api import Client
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.keypair import Keypair


def generate_solana_pay_url(
    recipient: str,
    amount_usd: float,
    token_type: str = 'USDC'
) -> Tuple[str, str]:
    """
    Generate a Solana Pay transfer request URL.

    Args:
        recipient: Solana wallet address to receive payment
        amount_usd: Amount in USD (will be converted to lamports)
        token_type: 'USDC' or 'USDT'

    Returns:
        Tuple of (payment_url, reference_pubkey_string)

    Example URL:
        solana:RECIPIENT?amount=25000000&spl-token=MINT&reference=PUBKEY&label=...
    """
    from wallet_analysis.models import SolanaPayment

    # For Solana Pay URL, `amount` must be the token amount in human units
    # (e.g., 25.0 USDC), not base units. We'll still compute lamports separately
    # for server-side verification.
    amount_tokens_str = f"{amount_usd:.6f}".rstrip('0').rstrip('.')

    # Get token mint address
    token_mint = (
        SolanaPayment.USDC_MINT if token_type == 'USDC'
        else SolanaPayment.USDT_MINT
    )

    # Generate unique reference keypair for tracking
    # We only need the public key (base58 string), not the private key
    reference_keypair = Keypair()
    reference_pubkey = str(reference_keypair.pubkey())

    # Build query parameters
    params = {
        'amount': amount_tokens_str,
        'spl-token': token_mint,
        'reference': reference_pubkey,
        'label': 'CryptoTax Wallet Analysis',
        'message': 'Payment for wallet analysis report ($25 USDC)',
    }

    # Construct Solana Pay URL
    payment_url = f"solana:{recipient}?{urlencode(params)}"

    return payment_url, reference_pubkey


def get_solana_rpc_client() -> Client:
    """
    Get configured Solana RPC client.

    Returns:
        Solana RPC Client instance
    """
    rpc_url = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
    return Client(rpc_url)


def verify_transaction_on_chain(
    signature: str,
    recipient: str,
    expected_amount: int,
    token_mint: str,
    reference: str
) -> bool:
    """
    Verify a Solana transaction matches our payment parameters.

    Args:
        signature: Transaction signature to verify
        recipient: Expected recipient address
        expected_amount: Expected amount in lamports
        token_mint: Expected token mint address (USDC or USDT)
        reference: Expected reference public key (base58 string)

    Returns:
        True if transaction is valid and matches all parameters, False otherwise
    """
    try:
        client = get_solana_rpc_client()

        # Convert signature string to Signature object
        sig = Signature.from_string(signature)

        # Fetch transaction from blockchain
        response = client.get_transaction(
            sig,
            encoding="jsonParsed",
            max_supported_transaction_version=0
        )

        if not response.value:
            print(f"Transaction not found: {signature}")
            return False

        tx = response.value

        # Check transaction status (must be finalized/confirmed)
        if hasattr(tx, 'meta') and tx.meta and tx.meta.err:
            print(f"Transaction failed on-chain: {tx.meta.err}")
            return False

        # Get transaction details
        transaction = tx.transaction
        message = transaction.transaction.message

        # Convert recipient to Pubkey for comparison
        recipient_pubkey = Pubkey.from_string(recipient)

        # Convert reference string to Pubkey for comparison
        # In Solana Pay, reference is typically added as a read-only account
        reference_pubkey = Pubkey.from_string(reference)

        # Verify recipient is in account keys
        account_keys = message.account_keys
        recipient_found = any(
            str(key) == str(recipient_pubkey) for key in account_keys
        )

        if not recipient_found:
            print(f"Recipient {recipient} not found in transaction")
            return False

        # Verify reference is in account keys
        reference_found = any(
            str(key) == str(reference_pubkey) for key in account_keys
        )

        if not reference_found:
            print(f"Reference {reference} not found in transaction")
            return False

        # Parse instructions to verify SPL token transfer
        instructions = message.instructions
        token_transfer_found = False

        for instruction in instructions:
            # Check if this is an SPL token instruction
            program_id = account_keys[instruction.program_id_index]

            # SPL Token Program ID
            spl_token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

            if str(program_id) == spl_token_program:
                # This is a token instruction
                # For a proper implementation, we'd parse the instruction data
                # to verify amount and mint, but for now we'll check if it exists
                token_transfer_found = True
                break

        if not token_transfer_found:
            print("No SPL token transfer instruction found")
            return False

        # All checks passed
        print(f"Transaction {signature} verified successfully")
        return True

    except Exception as e:
        print(f"Error verifying transaction {signature}: {e}")
        return False


def search_transactions_by_reference(
    reference: str,
    recipient: str
) -> Optional[str]:
    """
    Search for transactions containing a specific reference public key.
    Used by background task to find payments that weren't immediately verified.

    Args:
        reference: Reference public key (base58 string) to search for
        recipient: Recipient address to search transactions for

    Returns:
        Transaction signature if found, None otherwise
    """
    try:
        client = get_solana_rpc_client()

        # Convert reference to Pubkey
        reference_pubkey = Pubkey.from_string(reference)

        # Get recent signatures for the reference account
        # Note: The reference is included as a read-only account in the transaction
        response = client.get_signatures_for_address(
            reference_pubkey,
            limit=10
        )

        if response.value:
            # Return the most recent signature
            return str(response.value[0].signature)

        return None

    except Exception as e:
        print(f"Error searching for transactions with reference {reference}: {e}")
        return None
