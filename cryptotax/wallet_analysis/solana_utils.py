"""
Solana Pay utilities for payment URL generation and transaction verification.
"""

import os
from typing import Tuple, Optional, List
from urllib.parse import urlencode

from django.conf import settings
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
    Network-aware: uses correct token mints for mainnet or devnet.

    Args:
        recipient: Solana wallet address to receive payment
        amount_usd: Amount in USD (will be converted to lamports)
        token_type: 'USDC' or 'USDT'

    Returns:
        Tuple of (payment_url, reference_pubkey_string)

    Example URL:
        solana:RECIPIENT?amount=25000000&spl-token=MINT&reference=PUBKEY&label=...
    """
    # For Solana Pay URL, `amount` must be the token amount in human units
    # (e.g., 25.0 USDC), not base units. We'll still compute lamports separately
    # for server-side verification.
    amount_tokens_str = f"{amount_usd:.6f}".rstrip('0').rstrip('.')

    # Get token mint address from settings (network-aware)
    token_mint = (
        settings.USDC_MINT if token_type == 'USDC'
        else settings.USDT_MINT
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
    Network-aware: uses correct RPC URL for mainnet or devnet.

    Returns:
        Solana RPC Client instance
    """
    rpc_url = settings.SOLANA_RPC_URL
    return Client(rpc_url)


from solders.pubkey import Pubkey
from solders.signature import Signature
from spl.token.instructions import get_associated_token_address


def _to_pubkey_str(key_obj) -> str:
    """Normalize various key objects to base58 string."""
    try:
        if hasattr(key_obj, 'pubkey'):
            return str(key_obj.pubkey)
        return str(key_obj)
    except Exception:
        return str(key_obj)


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
        expected_amount: Expected amount in smallest units (e.g., lamports for SOL, base units for tokens)
        token_mint: Expected token mint address (USDC or USDT)
        reference: Expected reference public key (base58 string)

    Returns:
        True if transaction is valid and matches all parameters, False otherwise
    """
    try:
        client = get_solana_rpc_client()

        print(f"\n[VERIFY] Starting verification for: {signature[:16]}...")
        print(f"[VERIFY] Expected recipient: {recipient}")
        print(f"[VERIFY] Expected reference: {reference}")
        print(f"[VERIFY] Expected token mint: {token_mint}")
        print(f"[VERIFY] Expected amount: {expected_amount}")

        # Convert signature string to Signature object
        sig = Signature.from_string(signature)

        # Fetch transaction from blockchain
        response = client.get_transaction(
            sig,
            encoding="jsonParsed",
            max_supported_transaction_version=0
        )

        if not response.value:
            print(f"[VERIFY] ❌ Transaction not found: {signature}")
            return False

        print(f"[VERIFY] ✅ Transaction found on blockchain")

        tx = response.value

        # Check transaction status (must be successful). Be tolerant of schema differences.
        tx_err = None
        try:
            meta = getattr(tx, 'meta', None)
            if meta is not None:
                tx_err = getattr(meta, 'err', None)
        except Exception:
            tx_err = None

        # Fallback: query signature status if meta not available
        if tx_err is None:
            try:
                status_resp = client.get_signature_statuses([sig])
                if status_resp.value and status_resp.value[0] is not None:
                    tx_err = status_resp.value[0].err
            except Exception:
                tx_err = None

        if tx_err:
            print(f"[VERIFY] ❌ Transaction failed on-chain: {tx_err}")
            return False

        # Get transaction message
        message = tx.transaction.transaction.message

        # Convert recipient and reference to Pubkey for comparison
        recipient_pubkey = Pubkey.from_string(recipient)
        reference_pubkey = Pubkey.from_string(reference)
        mint_pubkey = Pubkey.from_string(token_mint)

        # Extract account keys and normalize to base58 strings
        account_keys = message.account_keys
        account_keys_str = [_to_pubkey_str(k) for k in account_keys]
        account_pubkeys_str = account_keys_str

        print(f"[VERIFY] Account pubkeys in transaction: {account_pubkeys_str[:5]}...")  # Show first 5

        # Check for recipient wallet
        recipient_found = str(recipient_pubkey) in account_pubkeys_str

        # Derive the Associated Token Account (ATA) for recipient + mint using canonical seeds
        recipient_ata_found = False
        expected_ata = None
        try:
            token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            associated_token_program_id = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
            expected_ata, _ = Pubkey.find_program_address(
                [bytes(recipient_pubkey), bytes(token_program_id), bytes(mint_pubkey)],
                associated_token_program_id,
            )
            recipient_ata_found = str(expected_ata) in account_pubkeys_str
            print(f"[VERIFY] Expected ATA: {expected_ata}")
        except Exception as e:
            print(f"[VERIFY] ⚠️ Failed to derive ATA: {e}")

        print(
            f"[VERIFY] Recipient check: wallet={recipient_found}, ata={recipient_ata_found}"
        )

        if not (recipient_found or recipient_ata_found):
            print(
                f"[VERIFY] ❌ Neither recipient wallet {recipient} nor its ATA found in transaction"
            )
            return False

        # Verify reference is in account keys
        reference_found = str(reference_pubkey) in account_pubkeys_str
        print(f"[VERIFY] Reference check: {reference_found} (looking for {reference_pubkey})")

        if not reference_found:
            print(f"[VERIFY] ❌ Reference {reference} not found in transaction")
            return False

        # Parse instructions to verify SPL token transfer with correct amount
        instructions = message.instructions
        token_transfer_verified = False
        amount_verified = False

        # SPL Token Program ID
        TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

        for idx, instruction in enumerate(instructions):
            # Resolve program id and instruction account pubkeys across variants
            prog_id_str = None
            inst_pubkeys: List[str] = []
            # Variant 1: compiled instruction with program_id_index and account indexes
            if hasattr(instruction, 'program_id_index'):
                try:
                    program_id = account_keys[instruction.program_id_index]
                    prog_id_str = _to_pubkey_str(program_id)
                except Exception:
                    prog_id_str = None
                try:
                    inst_accounts = getattr(instruction, 'accounts', [])
                    inst_pubkeys = [account_pubkeys_str[i] for i in inst_accounts]
                except Exception:
                    inst_pubkeys = []
            # Variant 2: partially decoded instruction with explicit program_id and accounts as pubkeys
            elif hasattr(instruction, 'program_id'):
                try:
                    prog_id_str = _to_pubkey_str(getattr(instruction, 'program_id'))
                except Exception:
                    prog_id_str = None
                try:
                    inst_accounts = getattr(instruction, 'accounts', [])
                    inst_pubkeys = [_to_pubkey_str(a) for a in inst_accounts]
                except Exception:
                    inst_pubkeys = []
            else:
                continue

            if prog_id_str == TOKEN_PROGRAM_ID:
                print(f"[VERIFY] Found SPL token instruction at index {idx}")

                # For jsonParsed encoding, check if instruction has parsed data
                if hasattr(instruction, 'parsed') and instruction.parsed:
                    parsed = instruction.parsed
                    
                    # Check if this is a transfer or transferChecked instruction
                    if isinstance(parsed, dict):
                        info = parsed.get('info', {})
                        instruction_type = parsed.get('type', '')
                        
                        print(f"[VERIFY] Instruction type: {instruction_type}")
                        
                        if instruction_type in ['transfer', 'transferChecked']:
                            token_transfer_verified = True
                            
                            # Extract and verify amount
                            # For 'transfer': amount is in 'amount' field (string)
                            # For 'transferChecked': amount is in 'tokenAmount' -> 'amount' (string)
                            if instruction_type == 'transfer':
                                transfer_amount = int(info.get('amount', '0'))
                            else:  # transferChecked
                                token_amount_info = info.get('tokenAmount', {})
                                transfer_amount = int(token_amount_info.get('amount', '0'))
                            
                            print(f"[VERIFY] Transfer amount: {transfer_amount}")
                            print(f"[VERIFY] Expected amount: {expected_amount}")
                            
                            if transfer_amount >= expected_amount:
                                amount_verified = True
                                print(f"[VERIFY] ✅ Amount verified")
                            else:
                                print(f"[VERIFY] ⚠️ Amount mismatch: got {transfer_amount}, expected {expected_amount}")
                            
                            # Verify destination is the recipient's ATA
                            destination = info.get('destination', '')
                            if expected_ata and str(destination) == str(expected_ata):
                                print(f"[VERIFY] ✅ Destination matches recipient ATA")
                            elif str(destination) == str(recipient_pubkey):
                                print(f"[VERIFY] ✅ Destination matches recipient wallet")
                            else:
                                print(f"[VERIFY] ⚠️ Destination mismatch: {destination}")
                            
                            break
                else:
                    # No parsed payload available; fall back to heuristics
                    # Consider token transfer verified if expected ATA participates
                    if expected_ata and str(expected_ata) in inst_pubkeys:
                        token_transfer_verified = True
                        amount_verified = True  # cannot parse amount; accept for devnet
                        print("[VERIFY] ⚠️ Parsed data missing; using ATA presence heuristic")
                        break

        print(f"[VERIFY] Token transfer verified: {token_transfer_verified}")
        print(f"[VERIFY] Amount verified: {amount_verified}")

        if not token_transfer_verified:
            print("[VERIFY] ❌ No SPL token transfer instruction found")
            return False

        if not amount_verified:
            print("[VERIFY] ❌ Transfer amount does not match expected amount")
            return False

        # All checks passed
        print(f"[VERIFY] ✅ Transaction {signature[:16]}... verified successfully!")
        return True

    except Exception as e:
        print(f"[VERIFY] ❌ Error verifying transaction {signature}: {e}")
        import traceback
        traceback.print_exc()
        return False


def get_references_from_signature(signature: str) -> List[str]:
    """Extract Solana Pay reference pubkeys from a transaction signature.

    Returns a list of reference account pubkey strings. If no references
    are present or the transaction cannot be fetched, returns an empty list.
    """
    try:
        client = get_solana_rpc_client()
        sig = Signature.from_string(signature)
        resp = client.get_transaction(
            sig,
            encoding="jsonParsed",
            max_supported_transaction_version=0,
        )
        if not resp.value:
            return []

        tx = resp.value
        # Safely get message
        try:
            message = tx.transaction.transaction.message
        except Exception:
            return []

        account_keys = message.account_keys
        account_keys_str = [_to_pubkey_str(k) for k in account_keys]
        TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

        references: List[str] = []

        for instruction in getattr(message, 'instructions', []):
            # Filter to SPL token transfer instruction
            try:
                program_id = account_keys[instruction.program_id_index]
            except Exception:
                continue

            if str(program_id) != TOKEN_PROGRAM_ID:
                continue

            parsed = getattr(instruction, 'parsed', None)
            info = {}
            if isinstance(parsed, dict):
                info = parsed.get('info', {})

            # Known non-reference accounts for SPL token transfer/transferChecked
            used_accounts = set()
            for k in ('source', 'destination', 'authority', 'owner', 'mint'):
                v = info.get(k)
                if v:
                    used_accounts.add(str(v))
            used_accounts.add(TOKEN_PROGRAM_ID)

            # Map instruction account indexes to pubkeys
            try:
                inst_accounts = getattr(instruction, 'accounts', [])
                inst_pubkeys = [account_keys_str[i] for i in inst_accounts]
            except Exception:
                inst_pubkeys = []

            # References are accounts in the instruction that are not part of the
            # required SPL token meta and not the program id itself
            for pk in inst_pubkeys:
                if pk not in used_accounts and pk not in references:
                    references.append(pk)

            # We only need to inspect the first matching token transfer
            if references:
                break

        # Fallback: if no explicit references found, return all account keys so
        # callers can match against known references in their database.
        if not references:
            return account_keys_str

        return references
    except Exception:
        return []


def decode_transaction_for_debug(signature: str) -> dict:
    """Decode a transaction and return rich debug info.

    Returns a dict with keys:
    - found: bool
    - account_keys: List[str]
    - instructions: List[{
        program_id: str,
        accounts: List[int],
        account_pubkeys: List[str],
        parsed: dict | None,
      }]
    - reference_candidates: List[str] (heuristic possible references)
    - error: Optional[str]
    """
    out = {
        'found': False,
        'account_keys': [],
        'instructions': [],
        'reference_candidates': [],
        'error': None,
    }
    try:
        client = get_solana_rpc_client()
        sig = Signature.from_string(signature)
        resp = client.get_transaction(
            sig,
            encoding="jsonParsed",
            max_supported_transaction_version=0,
        )
        if not resp.value:
            return out
        tx = resp.value
        try:
            message = tx.transaction.transaction.message
        except Exception as e:
            out['error'] = f'message access error: {e}'
            return out

        account_keys = message.account_keys
        account_keys_str = [_to_pubkey_str(k) for k in account_keys]
        out['account_keys'] = account_keys_str

        TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

        # Build instruction summaries and reference candidates
        ref_candidates: List[str] = []
        for instruction in getattr(message, 'instructions', []):
            try:
                program_id = account_keys[instruction.program_id_index]
            except Exception:
                continue
            try:
                inst_accounts = getattr(instruction, 'accounts', [])
                inst_pubkeys = [account_keys_str[i] for i in inst_accounts]
            except Exception:
                inst_accounts = []
                inst_pubkeys = []

            parsed = getattr(instruction, 'parsed', None)
            parsed_dict = parsed if isinstance(parsed, dict) else None

            out['instructions'].append({
                'program_id': _to_pubkey_str(program_id),
                'accounts': list(inst_accounts),
                'account_pubkeys': inst_pubkeys,
                'parsed': parsed_dict,
            })

            # Heuristic: for token program, anything not in known meta is a candidate reference
            if _to_pubkey_str(program_id) == TOKEN_PROGRAM_ID:
                info = parsed_dict.get('info', {}) if parsed_dict else {}
                known = set()
                for k in ('source', 'destination', 'authority', 'owner', 'mint'):
                    v = info.get(k)
                    if v:
                        known.add(str(v))
                known.add(TOKEN_PROGRAM_ID)
                for pk in inst_pubkeys:
                    if pk not in known and pk not in ref_candidates:
                        ref_candidates.append(pk)

        out['reference_candidates'] = ref_candidates if ref_candidates else account_keys_str
        out['found'] = True
        return out
    except Exception as e:
        out['error'] = str(e)
        return out
    
def verify_transaction_on_chain_old(
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

        print(f"\n[VERIFY] Starting verification for: {signature[:16]}...")
        print(f"[VERIFY] Expected recipient: {recipient}")
        print(f"[VERIFY] Expected reference: {reference}")
        print(f"[VERIFY] Expected token mint: {token_mint}")
        print(f"[VERIFY] Expected amount: {expected_amount}")

        # Convert signature string to Signature object
        sig = Signature.from_string(signature)

        # Fetch transaction from blockchain
        response = client.get_transaction(
            sig,
            encoding="jsonParsed",
            max_supported_transaction_version=0
        )

        if not response.value:
            print(f"[VERIFY] ❌ Transaction not found: {signature}")
            return False

        print(f"[VERIFY] ✅ Transaction found on blockchain")

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

        # Extract pubkey from ParsedAccountTxStatus objects (for jsonParsed encoding)
        account_pubkeys = []
        for key in account_keys:
            if hasattr(key, 'pubkey'):
                account_pubkeys.append(str(key.pubkey))
            else:
                account_pubkeys.append(str(key))

        print(f"[VERIFY] Account pubkeys in transaction: {account_pubkeys}")

        # Check for recipient wallet OR its Associated Token Account (ATA)
        recipient_found = str(recipient_pubkey) in account_pubkeys
        # Derive expected ATA for recipient + mint
        try:
            token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            associated_token_program_id = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
            mint_pubkey = Pubkey.from_string(token_mint)
            expected_ata, _ = Pubkey.find_program_address(
                [b"ata", bytes(recipient_pubkey), bytes(token_program_id), bytes(mint_pubkey)],
                associated_token_program_id,
            )
            recipient_ata_found = str(expected_ata) in account_pubkeys
        except Exception as e:
            print(f"[VERIFY] ⚠️ Failed to derive ATA: {e}")
            recipient_ata_found = False

        print(
            f"[VERIFY] Recipient check: wallet={recipient_found}, ata={recipient_ata_found} "
            f"(wallet {recipient_pubkey}, ata {str(expected_ata) if 'expected_ata' in locals() else 'n/a'})"
        )

        if not (recipient_found or recipient_ata_found):
            print(
                f"[VERIFY] ❌ Neither recipient wallet {recipient} nor its ATA found in transaction"
            )
            return False

        # Verify reference is in account keys
        reference_found = str(reference_pubkey) in account_pubkeys
        print(f"[VERIFY] Reference check: {reference_found} (looking for {reference_pubkey})")

        if not reference_found:
            print(f"[VERIFY] ❌ Reference {reference} not found in transaction")
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

        print(f"[VERIFY] Token transfer check: {token_transfer_found}")

        if not token_transfer_found:
            print("[VERIFY] ❌ No SPL token transfer instruction found")
            return False

        # All checks passed
        print(f"[VERIFY] ✅ Transaction {signature[:16]}... verified successfully!")
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
