// Parse every <script> block of a fetched page without executing it.
// new Function(src) runs the full JS parser and throws SyntaxError on bad
// syntax, but never evaluates the body — safe for DOM-dependent code.
const path = process.argv[2];
const html = await Bun.file(path).text();

const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1]);

console.log(`  script blocks found: ${blocks.length}`);
let bad = 0;
blocks.forEach((src, i) => {
  const lines = src.split("\n").length;
  try {
    new Function(src);
    console.log(`  block ${i + 1} (${lines} lines, ${src.length} bytes): PARSES OK`);
  } catch (e) {
    bad++;
    console.log(`  block ${i + 1} (${lines} lines): ${e.name}: ${e.message}`);
  }
});
console.log(bad ? `  => ${bad} block(s) FAILED TO PARSE` : "  => all blocks parse");
process.exit(bad ? 1 : 0);
