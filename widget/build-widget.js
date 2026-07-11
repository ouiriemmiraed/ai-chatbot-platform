const fs = require('fs');
const path = require('path');

const buildDir = path.join(__dirname, 'build');
const staticDir = path.join(buildDir, 'static');
const jsDir = path.join(staticDir, 'js');
const cssDir = path.join(staticDir, 'css');

async function bundle() {
    console.log('Bundling widget...');

    // Find JS file (entry point)
    const jsFiles = fs.readdirSync(jsDir).filter(f => f.match(/main\.[a-z0-9]+\.js$/));
    if (jsFiles.length === 0) throw new Error('No main JS file found');
    const mainJs = jsFiles[0];
    console.log(`Found JS: ${mainJs}`);

    // Find CSS file
    let cssContent = '';
    if (fs.existsSync(cssDir)) {
        const cssFiles = fs.readdirSync(cssDir).filter(f => f.match(/main\.[a-z0-9]+\.css$/));
        if (cssFiles.length > 0) {
            const mainCss = cssFiles[0];
            console.log(`Found CSS: ${mainCss}`);
            cssContent = fs.readFileSync(path.join(cssDir, mainCss), 'utf8');
            // Drop CSS source-map reference too (injected into the bundle below).
            cssContent = cssContent.replace(/\/\*# sourceMappingURL=.*?\*\//g, '');
        }
    }

    let jsContent = fs.readFileSync(path.join(jsDir, mainJs), 'utf8');

    // Strip any sourceMappingURL reference — we do NOT ship source maps for the
    // public widget (would expose readable source). Build with
    // GENERATE_SOURCEMAP=false too so no .map files are produced in the first place.
    jsContent = jsContent.replace(/\n?\/\/# sourceMappingURL=.*$/gm, '');

    // Inject CSS into JS
    if (cssContent) {
        const cssInjection = `
(function() {
  const style = document.createElement('style');
  style.textContent = ${JSON.stringify(cssContent)};
  document.head.appendChild(style);
})();
`;
        jsContent = cssInjection + jsContent;
    }

    // Write bundled file to build root
    const bundlePath = path.join(buildDir, 'widget.bundle.js');
    fs.writeFileSync(bundlePath, jsContent);
    console.log(`Widget bundled to: ${bundlePath}`);
}

bundle().catch(err => {
    console.error(err);
    process.exit(1);
});
