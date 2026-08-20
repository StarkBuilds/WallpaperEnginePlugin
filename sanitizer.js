const fs = require('fs');
const babel = require('@babel/core');

const file = process.argv[2];
if (!file) {
    console.error("Usage: node sanitizer.js <file.json>");
    process.exit(1);
}

let data;
try {
    data = JSON.parse(fs.readFileSync(file, 'utf8'));
} catch (e) {
    console.error("Failed to read JSON:", e.message);
    process.exit(1);
}

let modified = false;

function processJs(code) {
    // Wallpaper engine doesn't use real ES modules, strip import/export keywords
    code = code.replace(/^import\s+.*?\s+from\s+['"].*?['"];?\s*$/gm, '');
    code = code.replace(/^export\s+/gm, '');
    
    // linux-wallpaperengine bug: sometimes passes undefined to update(value) or init(value)
    // which causes TypeError: cannot read property 'slice' of undefined and aborts rendering.
    code = code.replace(/function\s+update\s*\(\s*(\w+)\s*\)\s*\{/g, "function update($1) { if ($1 === undefined) $1 = ''; ");
    code = code.replace(/function\s+init\s*\(\s*(\w+)\s*\)\s*\{/g, "function init($1) { if ($1 === undefined) $1 = ''; ");
    
    try {
        const res = babel.transformSync(code, {
            presets: [['@babel/preset-env', { targets: "defaults", modules: false }]],
            comments: false,
            compact: false,
            ast: false
        });
        return res.code;
    } catch (e) {
        console.error("Babel failed to parse script snippet:", e.message);
        return code;
    }
}

function walk(obj) {
    if (obj && typeof obj === 'object') {
        for (let key in obj) {
            if (key === 'clearcolor' && typeof obj[key] === 'string') {
                if (obj[key] !== "0.00000 0.00000 0.00000") {
                    obj[key] = "0.00000 0.00000 0.00000";
                    modified = true;
                }
            }
            if (key === 'bgColor' && typeof obj[key] === 'string') {
                if (obj[key] !== "0 0 0") {
                    obj[key] = "0 0 0";
                    modified = true;
                }
            }
            if (key === 'effects' && Array.isArray(obj[key])) {
                // linux-wallpaperengine struggles with many distortion/blur effects, resulting in mosaic or pixelated messes.
                // We strip these out to keep the wallpaper crisp, sacrificing the distortion effect for visual fidelity.
                const buggyEffects = ['blur', 'waterripple', 'waterwaves', 'waterflow', 'refraction', 'shake', 'watercaustics', 'blurprecise'];
                const originalLength = obj[key].length;
                obj[key] = obj[key].filter(effect => {
                    if (effect && effect.file && typeof effect.file === 'string') {
                        const fileLower = effect.file.toLowerCase();
                        for (let buggy of buggyEffects) {
                            if (fileLower.includes(`/${buggy}/`) || fileLower.startsWith(`effects/${buggy}/`) || fileLower.includes(`/${buggy}`)) {
                                return false;
                            }
                        }
                    }
                    return true;
                });
                if (obj[key].length !== originalLength) {
                    modified = true;
                }
            }
            if (key === 'script' && typeof obj[key] === 'string') {
                const oldCode = obj[key];
                const newCode = processJs(oldCode);
                if (oldCode !== newCode) {
                    obj[key] = newCode;
                    modified = true;
                }
            } else {
                walk(obj[key]);
            }
        }
    }
}

walk(data);

if (modified) {
    fs.writeFileSync(file, JSON.stringify(data, null, 4));
    console.log("Sanitized " + file);
} else {
    console.log("No scripts modified in " + file);
}
