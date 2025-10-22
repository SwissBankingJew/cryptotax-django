/**
 * Solana Pay Integration - Vanilla JavaScript
 *
 * This module handles Solana wallet connections and SPL token payments
 * for the CryptoTax wallet analysis service.
 */

import { Connection, PublicKey, Transaction } from '@solana/web3.js';
import { parseURL, createTransfer } from '@solana/pay';
// Polyfill Buffer for browser usage (required by web3.js deps)
import { Buffer } from 'buffer';
if (typeof window !== 'undefined' && !window.Buffer) {
    window.Buffer = Buffer;
}

/**
 * Configuration object - will be set by Django template
 */
let CONFIG = null;

/**
 * Initialize the payment system with configuration from Django
 */
function initializePayment(config) {
    CONFIG = config;
    console.log('Solana Pay initialized with config:', CONFIG);
}

/**
 * Detect available Solana wallet provider
 */
function getWalletProvider() {
    // Check for Phantom
    if (window.phantom?.solana?.isPhantom) {
        return window.phantom.solana;
    }

    // Check for Solflare
    if (window.solflare?.isSolflare) {
        return window.solflare;
    }

    // Check for Backpack
    if (window.backpack?.isBackpack) {
        return window.backpack;
    }

    // Fallback to generic window.solana
    if (window.solana) {
        return window.solana;
    }

    return null;
}

/**
 * Check if wallet is installed
 */
function isWalletInstalled() {
    return getWalletProvider() !== null;
}

/**
 * Connect to the user's Solana wallet
 */
async function connectWallet() {
    const provider = getWalletProvider();

    if (!provider) {
        throw new Error('No Solana wallet found. Please install Phantom, Solflare, or Backpack.');
    }

    try {
        const response = await provider.connect();
        return response.publicKey;
    } catch (error) {
        if (error.code === 4001) {
            throw new Error('Wallet connection rejected by user');
        }
        throw error;
    }
}

/**
 * Try to connect to wallet silently (if previously authorized)
 */
async function connectWalletSilently() {
    const provider = getWalletProvider();

    if (!provider) {
        return null;
    }

    try {
        const response = await provider.connect({ onlyIfTrusted: true });
        return response.publicKey;
    } catch (error) {
        // Silent failure - wallet not previously authorized
        return null;
    }
}

/**
 * Create and send payment transaction
 */
async function createAndSendPayment(paymentUrl, rpcUrl = 'https://api.mainnet-beta.solana.com') {
    const provider = getWalletProvider();

    if (!provider || !provider.publicKey) {
        throw new Error('Wallet not connected');
    }

    // Parse Solana Pay URL
    const { recipient, amount, splToken, reference, memo } = parseURL(paymentUrl);

    console.log('Payment details:', {
        recipient: recipient.toString(),
        amount: amount.toString(),
        splToken: splToken?.toString(),
        reference: reference?.map(r => r.toString()),
        memo
    });

    // Create connection
    const connection = new Connection(rpcUrl, 'confirmed');

    // Create transfer transaction
    const transaction = await createTransfer(connection, provider.publicKey, {
        recipient,
        amount,
        splToken,
        reference,
        memo
    });

    // Get recent blockhash
    const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash('confirmed');
    transaction.recentBlockhash = blockhash;
    transaction.feePayer = provider.publicKey;

    console.log('Transaction created, requesting signature...');

    // Sign and send transaction
    const { signature } = await provider.signAndSendTransaction(transaction);

    console.log('Transaction sent:', signature);

    // Confirm transaction
    const confirmation = await connection.confirmTransaction({
        signature,
        blockhash,
        lastValidBlockHeight
    }, 'confirmed');

    if (confirmation.value.err) {
        throw new Error('Transaction failed: ' + JSON.stringify(confirmation.value.err));
    }

    console.log('Transaction confirmed:', signature);

    return signature;
}

/**
 * Verify payment with backend
 */
async function verifyPaymentWithBackend(orderId, signature) {
    const response = await fetch('/api/payment-verify/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({
            order_id: orderId,
            signature: signature
        })
    });

    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();

    if (!data.success) {
        throw new Error(data.message || 'Payment verification failed');
    }

    return data;
}

/**
 * Get CSRF token from cookie
 */
function getCsrfToken() {
    const name = 'csrftoken';
    const cookies = document.cookie.split(';');

    for (let cookie of cookies) {
        cookie = cookie.trim();
        if (cookie.startsWith(name + '=')) {
            return cookie.substring(name.length + 1);
        }
    }

    return '';
}

/**
 * Main payment flow
 */
async function processPayment(config, callbacks = {}) {
    const {
        onStatusChange = () => {},
        onSuccess = () => {},
        onError = () => {}
    } = callbacks;

    try {
        // Connect wallet
        onStatusChange('Connecting to wallet...', 'info');
        const publicKey = await connectWallet();
        console.log('Connected to wallet:', publicKey.toString());

        // Create and send payment
        onStatusChange('Creating payment transaction...', 'info');
        const signature = await createAndSendPayment(
            config.paymentUrl,
            config.rpcUrl || 'https://api.mainnet-beta.solana.com'
        );

        // Verify with backend
        onStatusChange('Verifying payment...', 'info');
        const result = await verifyPaymentWithBackend(config.orderId, signature);

        onStatusChange(result.message || 'Payment confirmed!', 'success');
        onSuccess(result, signature);

    } catch (error) {
        console.error('Payment error:', error);

        let errorMessage = error.message || 'Payment failed';

        if (error.message?.includes('rejected')) {
            errorMessage = 'Transaction rejected by user';
        } else if (error.message?.includes('insufficient')) {
            errorMessage = 'Insufficient funds in wallet';
        } else if (error.message?.includes('No Solana wallet')) {
            errorMessage = 'No Solana wallet detected. Please install Phantom, Solflare, or Backpack.';
        }

        onStatusChange(errorMessage, 'error');
        onError(error);
    }
}

// Expose functions to global scope for Django templates
window.SolanaPayment = {
    initialize: initializePayment,
    isWalletInstalled,
    connectWallet,
    connectWalletSilently,
    processPayment,
    verifyPaymentWithBackend
};

console.log('Solana Payment module loaded');
