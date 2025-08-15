import fs from 'fs';
import yaml from 'js-yaml';
import { encoding_for_model } from '@dqbd/tiktoken';

const INPUT = '/workspace/rest-api-description/descriptions/api.github.com/dereferenced/api.github.com.deref.yaml';
const OUTPUT = '/workspace/context1.txt';
const TOKEN_LIMIT = 200_000;
const MODEL = 'gpt-4o-mini';

const HTTP_METHODS = new Set(['get','put','post','patch','delete','options','head','trace']);

const PREFERRED_TAGS_ORDER = [
  'orgs','teams','repos','collaborators','members','actions','codespaces',
  'enterprise-admin','apps','users','scim','pulls','issues','projects',
  'migrations','dependabot','secret-scanning','code-scanning','copilot',
  'packages','hooks','webhooks','interactions','branch-protection',
  'environments','deployments','repository-invitations','billing','audit-log','admin'
];

function countTokens(encoder, text){
  return encoder.encode(text).length;
}

function buildDocForTags(data, includedTags, includeComponents){
  const allPaths = data.paths || {};
  const filteredPaths = {};
  let numOps = 0;
  for(const path of Object.keys(allPaths).sort()){
    const methods = allPaths[path];
    if(typeof methods !== 'object') continue;
    const newMethods = {};
    for(const [method, op] of Object.entries(methods)){
      if(!HTTP_METHODS.has(method)) continue;
      if(typeof op !== 'object') continue;
      const tags = op.tags || [];
      if(tags.some(t => includedTags.has(t)) || (tags.length === 0 && includedTags.has('__untagged__'))){
        newMethods[method] = op;
        numOps += 1;
      }
    }
    if(Object.keys(newMethods).length > 0){
      filteredPaths[path] = newMethods;
    }
  }
  const out = {
    openapi: data.openapi || '3.0.3',
    info: data.info,
    paths: filteredPaths
  };
  if(includeComponents && data.components){
    const compsOut = {};
    if(data.components.securitySchemes){
      compsOut.securitySchemes = data.components.securitySchemes;
    }
    if(Object.keys(compsOut).length){
      out.components = compsOut;
    }
  }
  const text = yaml.dump(out, { noRefs: true, lineWidth: -1, sortKeys: false });
  return { text, numOps };
}

async function main(){
  const raw = fs.readFileSync(INPUT, 'utf-8');
  const data = yaml.load(raw);
  const allPaths = data.paths || {};

  const sizeByTag = new Map();
  const availableTags = new Set();

  for(const [path, methods] of Object.entries(allPaths)){
    if(typeof methods !== 'object') continue;
    for(const [method, op] of Object.entries(methods)){
      if(!HTTP_METHODS.has(method)) continue;
      if(typeof op !== 'object') continue;
      const tags = op.tags || ['__untagged__'];
      const opYaml = yaml.dump({ [path]: { [method]: op } }, { noRefs: true, lineWidth: -1, sortKeys: false });
      const est = opYaml.length;
      for(const tag of tags){
        availableTags.add(tag);
        sizeByTag.set(tag, (sizeByTag.get(tag) || 0) + est);
      }
    }
  }

  const preferredPresent = PREFERRED_TAGS_ORDER.filter(t => availableTags.has(t));
  const remaining = [...availableTags].filter(t => !preferredPresent.includes(t));
  remaining.sort((a,b) => (sizeByTag.get(b) || 0) - (sizeByTag.get(a) || 0));
  const orderedTags = [...preferredPresent, ...remaining];

  const encoder = encoding_for_model(MODEL);

  let included = new Set();
  let bestText = '';
  let bestTokens = 0;
  let bestNumOps = 0;

  for(const tag of orderedTags){
    const trial = new Set([...included, tag]);
    const { text, numOps } = buildDocForTags(data, trial, false);
    const tokens = countTokens(encoder, text);
    if(tokens <= TOKEN_LIMIT){
      included = trial;
      bestText = text;
      bestTokens = tokens;
      bestNumOps = numOps;
    }
  }

  const withComp = buildDocForTags(data, included, true);
  const tokensWithComp = countTokens(encoder, withComp.text);
  if(tokensWithComp <= TOKEN_LIMIT){
    bestText = withComp.text;
    bestTokens = tokensWithComp;
  }

  fs.writeFileSync(OUTPUT, bestText);
  const numLines = bestText.split('\n').length;
  console.log(`Wrote ${OUTPUT}`);
  console.log(`Included tags (${included.size}): ${JSON.stringify([...included])}`);
  console.log(`Token count: ${bestTokens}`);
  console.log(`Line count: ${numLines}`);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
