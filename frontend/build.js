/**
 * esbuild configuration for bundling Solana Pay integration
 *
 * This bundles our vanilla JavaScript + Solana libraries into a single file
 * that can be served by Django's static file system.
 */

const esbuild = require('esbuild');
const path = require('path');

// Check if we're in watch mode
const watchMode = process.argv.includes('--watch');

// Output directory - Django's static files
const outputDir = path.join(__dirname, '../cryptotax/static/js');
const outputFile = path.join(outputDir, 'solana-payment.bundle.js');

// Build configuration
const buildOptions = {
    entryPoints: ['src/solana-payment.js'],
    bundle: true,
    outfile: outputFile,
    format: 'iife',  // Immediately Invoked Function Expression - works with <script> tags
    platform: 'browser',
    target: ['es2020'],
    sourcemap: !watchMode ? false : 'inline',  // Source maps for development
    minify: !watchMode,  // Minify for production
    logLevel: 'info',
};

async function build() {
    try {
        if (watchMode) {
            console.log('🔨 Building in watch mode...');
            console.log(`📦 Output: ${outputFile}`);

            const ctx = await esbuild.context(buildOptions);
            await ctx.watch();

            console.log('👀 Watching for changes...');
        } else {
            console.log('🔨 Building production bundle...');
            console.log(`📦 Output: ${outputFile}`);

            await esbuild.build(buildOptions);

            console.log('✅ Build complete!');
        }
    } catch (error) {
        console.error('❌ Build failed:', error);
        process.exit(1);
    }
}

build();
