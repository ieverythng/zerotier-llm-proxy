"""Offline validation of the pinned configuration-only compaction bundle."""

import json
import os
import subprocess
import unittest
from pathlib import Path
from xml.etree import ElementTree


BUNDLE = Path(__file__).resolve().parents[1] / "plugins" / "watson-inkling-compaction"
ENGINE = "0.2.2-rc.5"
CANDIDATE = Path(os.environ.get("DSH_COMPACTION_CANDIDATE", str(BUNDLE.with_name("watson-inkling-compaction-8192"))))
HAS_DSH = bool(os.environ.get("DSH_NODE_EXECUTABLE") and os.environ.get("DSH_INSTALL_DIR"))

# Resolve the packages from the actual installed DSH, not global Node modules.
VALIDATE_DSH = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire, stripTypeScriptTypes } = require('node:module');
const { pathToFileURL } = require('node:url');
const req = createRequire(path.join(process.env.DSH_INSTALL_DIR, 'package.json'));
const yaml = req('js-yaml');
const schema = yaml.DEFAULT_SCHEMA.extend(new yaml.Type('tag:yaml.org,2002:js', {
  kind: 'scalar',
  construct: expr => ({ unevaluatedCordisExpression: expr }),
}));
const readYaml = file => yaml.load(fs.readFileSync(file, 'utf8'), {schema});
const src = path.dirname(req.resolve('@deepseek-ai/dsh-web-app/package.json'));
const shipped = readYaml(path.join(src, 'presets', 'standard.patch.yml'));
const bundle = readYaml(path.join(process.argv[1], 'cordis.patch.yml'));
const candidatePath = process.argv[2];
const candidate = candidatePath && readYaml(path.join(candidatePath, 'cordis.patch.yml'));
assert.equal(req('@deepseek-ai/dsh/package.json').version, '0.2.2-rc.5');
assert.equal(req('@deepseek-ai/dsh-compaction-basic/package.json').version, '0.2.2-rc.5');
assert.equal(shipped.length, 1);
assert.equal(shipped[0].insert.length, 1);
assert.equal(bundle.length, 1);
const getBasic = preset => preset.config.plugins.find(row => row.id === 'compaction')
  .config.find(row => row.id === 'compaction-basic');
const target = {provider: 'watson-local', model: 'inkling2-mini-long'};
const intended = {modelPolicies:[{...target, headroomTokens:8192, maxTokens:4096}]};
const shippedBasic = getBasic(shipped[0].insert[0]);
assert.equal(shippedBasic.config, undefined);
const bundleBasic = getBasic(bundle[0]);
assert.deepEqual(bundleBasic.config, intended);
shippedBasic.config = intended;
assert.deepEqual(bundle, shipped[0].insert,
  'Every non-compaction field of the shipped Standard preset must be preserved');

(async () => {
  const source = fs.readFileSync(req.resolve('@deepseek-ai/dsh-compaction-basic/src/config.ts'), 'utf8');
  const js = stripTypeScriptTypes(source, {mode:'transform'}).replace(
    /(['"])@deepseek-ai\/dsh-util-values\1/,
    JSON.stringify(pathToFileURL(req.resolve('@deepseek-ai/dsh-util-values')).href));
  const {resolveConfig, resolveTargetPolicy, resolveCompactSpec} =
    await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
  const oldConfig = resolveConfig();
  const newConfig = resolveConfig(bundleBasic.config);
  const spec = (config, output) => resolveCompactSpec(resolveTargetPolicy(config, target), 100096, output);
  const before = spec(oldConfig, 10000);
  const after = spec(newConfig, 10000);
  assert.equal(before.maxTokens, 65536);
  assert.equal(after.maxTokens, 4096);
  assert.equal(before.thresholdTokens, 24560);
  assert.equal(after.thresholdTokens, 80076);
  assert.equal(spec(newConfig, 4096).thresholdTokens, 80076);
  for (const other of [
    {provider:'watson-local', model:'inkling2-mini'},
    {provider:'watson-local', model:'saluki27b'},
    {provider:'another-provider', model:'inkling2-mini-long'},
  ]) {
    assert.deepEqual(resolveTargetPolicy(oldConfig, other), resolveTargetPolicy(newConfig, other));
  }
  // Invoke the actual installed engine hook. No installed providers or session
  // databases are opened: the hardware/network seam is a deterministic LLM stub.
  const dshImport = async name => import(pathToFileURL(req.resolve(name)).href);
  const {BasicCompactionEngine} = await dshImport('@deepseek-ai/dsh-compaction-basic');
  const {Context} = await dshImport('@deepseek-ai/cordis');
  const captured = [];
  const fakeLlm = {async *stream(request) {
    const {provider, model, maxTokens, purpose} = request;
    captured.push({provider, model, maxTokens, purpose});
    assert.equal(request.messages.length, 2, 'Summary should replay the fixture and append its instruction');
    yield {type:'text-delta', index:0, text:'Synthetic checkpoint'};
    yield {type:'finish', reason:{kind:'stop'}};
  }};
  const createEngine = config => {
    const ctx = new Context();
    ctx.provide('llm');
    ctx.set('llm', fakeLlm);
    return new BasicCompactionEngine(ctx, {...config, auto:false});
  };
  const baselineEngine = createEngine({});
  const engine = createEngine(bundleBasic.config);
  const input = {messages:[{role:'user', content:[{type:'text', text:'fixture'}]}]};
  const summarize = async (engine, model, chatCap) => {
    const agent = {options:{}, session:{id:'fixture-session', toolHistory:() => [],
      requestHeader:() => ({config:{provider:target.provider, model, maxTokens:chatCap}})}};
    const result = await engine.summarize(input, agent);
    assert.deepEqual(result.summary, [{type:'text', text:'Synthetic checkpoint'}]);
    assert.equal(result.maxTokens, captured.at(-1).maxTokens);
    assert.equal(result.model, model);
  };
  await summarize(baselineEngine, target.model, 10000);
  await summarize(engine, target.model, 10000);
  await summarize(engine, 'saluki27b', 4096);
  assert.deepEqual(captured, [
    {provider:target.provider, model:target.model, maxTokens:65536, purpose:'compaction'},
    {provider:target.provider, model:target.model, maxTokens:4096, purpose:'compaction'},
    {provider:target.provider, model:'saluki27b', maxTokens:65536, purpose:'compaction'},
  ]);
  const result = {preset:'standard', untouchedSiblingFields:true,
    summaryCap:after.maxTokens, originalSummaryCap:before.maxTokens,
    autoThreshold:after.thresholdTokens, originalAutoThreshold:before.thresholdTokens,
    capturedSummaryCaps:captured.map(row => row.maxTokens)};
  if (candidate) {
    const expected = structuredClone(bundle);
    getBasic(expected[0]).config.modelPolicies[0].maxTokens = 8192;
    assert.deepEqual(candidate, expected, 'Only the existing exact-route summary cap may change');
    const candidateBasic = getBasic(candidate[0]);
    const candidateConfig = resolveConfig(candidateBasic.config);
    const oldPolicy = resolveTargetPolicy(newConfig, target);
    const newPolicy = resolveTargetPolicy(candidateConfig, target);
    for (const [context, output] of [[100096, 4096], [150016, 10000]]) {
      const oldSpec = resolveCompactSpec(oldPolicy, context, output);
      const newSpec = resolveCompactSpec(newPolicy, context, output);
      assert.deepEqual(newSpec, {...oldSpec, maxTokens:8192});
    }
    for (const other of [
      {provider:'watson-local', model:'inkling2-mini'},
      {provider:'watson-local', model:'inkling2-mini-100k'},
      {provider:'watson-local', model:'saluki27b'},
      {provider:'another-provider', model:'inkling2-mini-long'},
    ]) {
      assert.deepEqual(resolveTargetPolicy(candidateConfig, other), resolveTargetPolicy(newConfig, other));
    }

    // The fake models a 6,000-token summary. It emits the actual DSH finish
    // variants, not a fabricated exception; the installed summarizer decides
    // whether it can return a checkpoint. Output units are synthetic, not an
    // estimate of any real model's tokenizer or summary quality.
    const requests = [];
    const required = 6000;
    const boundedStub = {async *stream(request) {
      const {provider, model, maxTokens, purpose} = request;
      assert.equal(provider, target.provider);
      assert.equal(model, target.model);
      assert.equal(purpose, 'compaction');
      assert.equal(request.messages.length, 2);
      assert.equal(request.messages[0].content[0].text, 'Synthetic inventory status: green.');
      requests.push(maxTokens);
      yield {type:'text-delta',index:0,text:maxTokens < required ? 'Partial checkpoint' : 'Synthetic inventory status: green.'};
      yield {type:'finish',reason:{kind:maxTokens < required ? 'max-tokens' : 'stop'}};
    }};
    const boundedEngine = config => {
      const ctx = new Context();
      ctx.provide('llm');
      ctx.set('llm', boundedStub);
      return new BasicCompactionEngine(ctx, {...config, auto:false});
    };
    const fixture = {messages:[{role:'user',content:[{type:'text',text:'Synthetic inventory status: green.'}]}]};
    const agent = {options:{}, session:{id:'synthetic-budget-fixture', toolHistory:() => [],
      requestHeader:() => ({config:{...target, maxTokens:10000}})}};
    await assert.rejects(boundedEngine(bundleBasic.config).summarize(fixture, agent), {
      code:'MAX_TOKENS', message:'summarization truncated at the token cap (incomplete checkpoint)',
    });
    const checkpoint = await boundedEngine(candidateBasic.config).summarize(fixture, agent);
    assert.equal(checkpoint.maxTokens, 8192);
    assert.deepEqual(checkpoint.summary, [{type:'text', text:'Synthetic inventory status: green.'}]);
    assert.deepEqual(requests, [4096,8192]);
    const liveSpec = resolveCompactSpec(newPolicy, 150016, 10000);
    result.candidate = {summaryCaps:requests, oldOutcome:'MAX_TOKENS', newOutcome:'stop',
      contextWindow:liveSpec.contextWindow, pressureThreshold:liveSpec.thresholdTokens,
      retainTokens:liveSpec.retainTokens, scope:'installed summarizer hook with fake LLM; no session persistence'};
  }
  console.log(JSON.stringify(result));
})().catch(err => { console.error(err.message); process.exitCode = 1; });
"""


class CompactionBundleTests(unittest.TestCase):
    def test_manifest_is_config_only_and_resources_are_declared(self):
        manifest = json.loads((BUNDLE / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["engines"], {"dsh": ENGINE})
        self.assertEqual(manifest["dsh"], {"bundle": {"patch": "./cordis.patch.yml"}})
        self.assertTrue(manifest["private"])
        self.assertEqual(
            set(manifest) & {"scripts", "main", "bin", "dependencies", "peerDependencies",
                             "devDependencies", "optionalDependencies"},
            set(),
        )
        self.assertEqual(manifest["exports"]["./locale/*.json"], "./locale/*.json")
        for name in ("en", "zh", "es"):
            meta = json.loads((BUNDLE / "locale" / f"{name}.json").read_text(encoding="utf-8"))["meta"]
            self.assertTrue(meta["title"] and meta["description"])
        icon = BUNDLE / manifest["exports"]["./icon"]
        self.assertLess(icon.stat().st_size, 256 * 1024)
        root = ElementTree.parse(icon).getroot()
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(root.attrib["viewBox"], "0 0 36 36")
        self.assertTrue((BUNDLE / "README.md").is_file())

    def validate_dsh(self, *args):
        process = subprocess.run(
            [os.environ["DSH_NODE_EXECUTABLE"], "-e", VALIDATE_DSH, "--", str(BUNDLE), *map(str, args)],
            env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"},
            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    @unittest.skipUnless(HAS_DSH, "requires the installed DSH Desktop runtime")
    def test_installed_resolver_and_shipped_declaration(self):
        self.assertEqual(self.validate_dsh(), {
            "preset": "standard", "untouchedSiblingFields": True,
            "summaryCap": 4096, "originalSummaryCap": 65536,
            "autoThreshold": 80076, "originalAutoThreshold": 24560,
            "capturedSummaryCaps": [65536, 4096, 65536],
        })

    @unittest.skipUnless(HAS_DSH, "requires the installed DSH Desktop runtime")
    def test_candidate_8192_rejects_truncation_and_preserves_exact_scope(self):
        candidate = CANDIDATE.resolve()
        current = json.loads((BUNDLE / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(current["version"], "1.0.0")
        manifest = json.loads((candidate / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, {**current, "version": "1.1.0"})
        for name in ("icon.svg", "locale/en.json", "locale/zh.json", "locale/es.json"):
            self.assertEqual((candidate / name).read_bytes(), (BUNDLE / name).read_bytes())
        self.assertTrue((candidate / "README.md").is_file())
        self.assertEqual(self.validate_dsh(candidate)["candidate"], {
            "summaryCaps": [4096, 8192], "oldOutcome": "MAX_TOKENS", "newOutcome": "stop",
            "contextWindow": 150016, "pressureThreshold": 120012, "retainTokens": 22402,
            "scope": "installed summarizer hook with fake LLM; no session persistence",
        })


if __name__ == "__main__":
    unittest.main()
