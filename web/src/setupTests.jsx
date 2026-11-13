// Jest 27's jsdom doesn't provide TextEncoder/TextDecoder, but react-router
// v7 (imported by tests that render router context) needs them at module
// load. Polyfill from Node's util — a no-op wherever they already exist.
const { TextEncoder, TextDecoder } = require("util");

if (typeof global.TextEncoder === "undefined") {
  global.TextEncoder = TextEncoder;
}
if (typeof global.TextDecoder === "undefined") {
  global.TextDecoder = TextDecoder;
}
