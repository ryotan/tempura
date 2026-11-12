import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { getConfig } from '../node_modules/renovate/dist/config/defaults.js';
import { GlobalConfig } from '../node_modules/renovate/dist/config/global.js';
import { resolveConfigPresets } from '../node_modules/renovate/dist/config/presets/index.js';
import { parsePreset } from '../node_modules/renovate/dist/config/presets/util.js';
import { mergeChildConfig } from '../node_modules/renovate/dist/config/utils.js';
import { defaultConfig, extractPackageFile } from '../node_modules/renovate/dist/modules/manager/copier/index.js';
import { extractPackageFile as extractPythonProject } from '../node_modules/renovate/dist/modules/manager/pep621/index.js';
import { api } from '../node_modules/renovate/dist/modules/versioning/pep440/index.js';
import { applyPackageRules } from '../node_modules/renovate/dist/util/package-rules/index.js';
import { calculateMinimumReleaseAgeMs } from '../node_modules/renovate/dist/util/minimum-release-age.js';
import { filterInternalChecks } from '../node_modules/renovate/dist/workers/repository/process/lookup/filter-checks.js';
import { parse as parseToml } from '../node_modules/renovate/dist/util/toml.js';
import { regexEngineStatus } from '../node_modules/renovate/dist/util/regex.js';

const root = new URL('../', import.meta.url);
GlobalConfig.set({ platform: 'github', localDir: fileURLToPath(root), presetCachePersistence: false });
const common = parsePreset(await readFile(new URL('renovate/default.json5', root), 'utf8'), 'default.json5');
const copier = parsePreset(await readFile(new URL('renovate/copier.json5', root), 'utf8'), 'copier.json5');
const { config: resolved } = await resolveConfigPresets(mergeChildConfig(common, copier));
const configuration = { ...getConfig(), ...resolved };

test('Preset evaluation uses native RE2', () => {
  assert.deepEqual(regexEngineStatus, { type: 'available' });
});

test('Source and consumer Copier execution use the same pinned release', async () => {
  const content = await readFile(new URL('pyproject.toml', root), 'utf8');
  const project = await extractPythonProject(content, 'pyproject.toml');
  const dependency = project.deps.find(({ depName }) => depName === 'copier');
  assert.equal(dependency.currentValue, copier.constraints.copier);
  assert.match(dependency.currentValue, /^==\d+\.\d+\.\d+$/);
});

test('Pinned dependency-free Pyflakes supports security updates while other packages retain their cooldown', async () => {
  const content = await readFile(new URL('pyproject.toml', root), 'utf8');
  const project = parseToml(content);
  const lock = parseToml(await readFile(new URL('uv.lock', root), 'utf8'));
  const extracted = await extractPythonProject(content, 'pyproject.toml');
  const dependency = extracted.deps.find(({ depName }) => depName === 'pyflakes');
  assert.match(dependency.currentValue, /^==\d+\.\d+\.\d+$/);
  assert.deepEqual({
    normalAge: configuration.minimumReleaseAge,
    securityAge: configuration.vulnerabilityAlerts.minimumReleaseAge,
    resolverAge: project.tool.uv['exclude-newer'],
    resolverExceptions: { ...project.tool.uv['exclude-newer-package'] },
    runtimeDependencies: lock.package.find(({ name }) => name === 'pyflakes').dependencies ?? [],
  }, {
    normalAge: '7 days',
    securityAge: '0 days',
    resolverAge: '7 days',
    resolverExceptions: { pyflakes: false },
    runtimeDependencies: [],
  });
});

for (const manager of ['pep621', 'renovate-config']) {
  for (const packageName of ['copier', 'pyflakes']) {
    test(`Source runtime grouping for ${manager} and ${packageName}`, async () => {
      const source = parsePreset(await readFile(new URL('.github/renovate.json5', root), 'utf8'), 'renovate.json5');
      const result = await applyPackageRules({ manager, packageName, packageRules: source.packageRules });
      assert.equal(result.groupName, packageName === 'copier' ? 'Copier runtime' : undefined);
    });
  }
}

async function templateConfiguration(source) {
  const dependency = extractPackageFile(
    `_commit: v0.1.0\n_src_path: ${source}\n`, '.copier-answers.shared-settings.yml',
  ).deps[0];
  return applyPackageRules({
    ...structuredClone(configuration), ...defaultConfig, ...dependency,
    manager: 'copier', packageFile: '.copier-answers.shared-settings.yml', currentVersion: 'v0.1.0',
  });
}

const sources = [
  ['HTTPS with suffix', 'https://github.com/ryotan/tempura.git', 'github-tags', 'ryotan/tempura'],
  ['HTTPS', 'https://github.com/ryotan/tempura', 'github-tags', 'ryotan/tempura'],
  ['Git-prefixed HTTPS', 'git+https://github.com/ryotan/tempura.git', 'github-tags', 'ryotan/tempura'],
  ['SSH', 'git@github.com:ryotan/tempura.git', 'github-tags', 'ryotan/tempura'],
  ['Another GitHub source', 'https://github.com/actions/checkout.git', 'git-tags',
    'https://github.com/actions/checkout.git'],
  ['Another Git host', 'https://git.example.invalid/owner/template.git', 'git-tags',
    'https://git.example.invalid/owner/template.git'],
];

for (const [name, source, datasource, packageName] of sources) {
  test(`Template lookup preserves its source and selects the expected datasource for ${name}`, async () => {
    const result = await templateConfiguration(source);
    assert.equal(result.depName, source);
    assert.equal(result.datasource, datasource);
    assert.equal(result.packageName, packageName);
    assert.equal(result.currentValue, 'v0.1.0');
    assert.equal(result.minimumReleaseAge, '7 days');
    assert.equal(result.minimumReleaseAgeBehaviour, 'timestamp-required');
  });
}

// Include Renovate's configured release-age buffer when testing the boundary.
const ages = [
  ['Missing timestamp', undefined, true],
  ['One hour before the effective minimum age', -1, true],
  ['At the effective minimum age', 0, false],
  ['One hour after the effective minimum age', 1, false],
];

for (const [name, hours, pending] of ages) {
  test(`Template release-age eligibility handles ${name}`, async () => {
    const config = await templateConfiguration('https://github.com/ryotan/tempura.git');
    const release = { version: 'v0.2.0' };
    if (hours !== undefined) {
      const age = calculateMinimumReleaseAgeMs(config) + hours * 3_600_000;
      release.releaseTimestamp = new Date(Date.now() - age).toISOString();
    }
    const result = await filterInternalChecks(config, api, 'minor', [release]);
    assert.deepEqual(result, { release, pendingChecks: pending, pendingReleases: [] });
  });
}

for (const manager of ['github-actions', 'copier']) {
  for (const depType of ['workflow', 'action']) {
    for (const packageName of ['ryotan/tempura', 'actions/checkout']) {
      for (const packageFile of ['.github/workflows/workflow-lint.yaml', '.github/workflows/build.yaml']) {
        test(`Reference ownership for ${manager}, ${depType}, ${packageName}, ${packageFile}`, async () => {
          const result = await applyPackageRules({
            manager, depType, packageName, packageFile, enabled: true, packageRules: copier.packageRules,
          });
          const generatedReference = manager === 'github-actions' && depType === 'workflow'
            && packageName === 'ryotan/tempura' && packageFile === '.github/workflows/workflow-lint.yaml';
          assert.equal(result.enabled, !generatedReference);
        });
      }
    }
  }
}
