// Araştırma arayüzü testlerini gerçek çeviri sözlüğüyle veya eski Türkçe yedekle kurar.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ui = require("../radar/static/ui-text.js");
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../radar/static/research-text.js"), "utf8"), {UfukUI: ui});
module.exports = language => ({language: language || "tr", t: language ? ui.createTranslator(language) : (key, values = {}) => key.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match)});
