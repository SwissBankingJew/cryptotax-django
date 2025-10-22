# Frontend JavaScript Build System

This directory contains the Node.js build system for the Solana Pay integration.

**NO FRAMEWORKS** - This is vanilla JavaScript, not React/Vue/Angular!

## Purpose

We need to bundle `@solana/web3.js` and `@solana/pay` libraries because:
- They use ES modules which don't work directly in browsers with CDN imports
- They have many dependencies that need to be bundled
- The CDN approach was mixing incompatible module systems (IIFE + ESM)

## What Gets Built

- **Source**: `src/solana-payment.js` - Vanilla JS wallet integration
- **Output**: `../cryptotax/static/js/solana-payment.bundle.js` - Single bundled file
- **Size**: ~343KB minified

## Usage

### Development (with auto-rebuild)
```bash
cd frontend
npm run dev
```

This watches for changes and automatically rebuilds when you edit `src/solana-payment.js`.

### Production Build
```bash
cd frontend
npm run build
```

This creates a minified bundle without source maps.

### First Time Setup
```bash
cd frontend
npm install
npm run build
```

## How It Works

1. **esbuild** bundles `src/solana-payment.js` + all dependencies
2. Output format is IIFE (Immediately Invoked Function Expression)
3. Bundle is written to Django's static directory
4. Django template loads it with `<script src="{% static 'js/solana-payment.bundle.js' %}">`
5. Vanilla JS in template calls `window.SolanaPayment.processPayment()`

## API Exposed to Templates

The bundle exposes `window.SolanaPayment` with:

- `initialize(config)` - Set configuration
- `isWalletInstalled()` - Check if Solana wallet exists
- `connectWallet()` - Connect to wallet (shows popup)
- `connectWalletSilently()` - Connect if previously authorized
- `processPayment(config, callbacks)` - Full payment flow
- `verifyPaymentWithBackend(orderId, signature)` - Verify with Django

## File Structure

```
frontend/
├── package.json          # Dependencies and scripts
├── build.js              # esbuild configuration
├── src/
│   └── solana-payment.js # Vanilla JS source
└── node_modules/         # Ignored by git
```

## Dependencies

- `@solana/web3.js` - Solana blockchain interaction
- `@solana/pay` - Solana Pay URL parsing and transaction creation
- `esbuild` (dev) - Fast JavaScript bundler

## Deployment

The **built bundle IS committed to git** at `cryptotax/static/js/solana-payment.bundle.js`.

This means:
- ✅ Production servers don't need Node.js
- ✅ Deployment is simple - just pull and run Django
- ⚠️ You must run `npm run build` before committing changes to `src/solana-payment.js`

## Notes

- This is NOT a frontend framework
- No hot module replacement (HMR)
- No TypeScript
- No JSX
- No routing
- Just vanilla JavaScript that gets bundled properly
