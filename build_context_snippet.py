import os
import sys
import yaml
import tiktoken
from collections import defaultdict, OrderedDict

INPUT = "/workspace/rest-api-description/descriptions/api.github.com/dereferenced/api.github.com.deref.yaml"
OUTPUT = "/workspace/context1.txt"
TOKEN_LIMIT = 200_000
ENCODING_NAME = "cl100k_base"

HTTP_METHODS = {"get","put","post","patch","delete","options","head","trace"}

# Prefer tags that are more likely to involve user interaction and access control
PREFERRED_TAGS_ORDER = [
    "orgs","teams","repos","collaborators","members","actions","codespaces",
    "enterprise-admin","apps","users","scim","pulls","issues","projects",
    "migrations","dependabot","secret-scanning","code-scanning","copilot",
    "packages","hooks","webhooks","interactions","branch-protection",
    "environments","deployments","repository-invitations","billing","audit-log",
    "admin"
]

enc = tiktoken.get_encoding(ENCODING_NAME)

def count_tokens(text: str) -> int:
    return len(enc.encode(text))

with open(INPUT, 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f)

all_paths = data.get('paths', {}) or {}

# Map tag -> cumulative string length estimate for operations carrying that tag
size_by_tag = defaultdict(int)
ops_by_tag = defaultdict(list)  # tag -> list of (path, method)

for path, methods in all_paths.items():
    if not isinstance(methods, dict):
        continue
    for method, op in methods.items():
        if method not in HTTP_METHODS:
            continue
        if not isinstance(op, dict):
            continue
        tags = op.get('tags') or []
        # length estimate based on YAML dump of just this op
        try:
            op_yaml = yaml.safe_dump({path: {method: op}}, sort_keys=False)
            est = len(op_yaml)
        except Exception:
            est = 1000
        for tag in tags or ["__untagged__"]:
            size_by_tag[tag] += est
            ops_by_tag[tag].append((path, method))

# Determine available tags in the spec
available_tags = list(size_by_tag.keys())

# Order tags: preferred first (in the specified order if present), then remaining by size desc
preferred_present = [t for t in PREFERRED_TAGS_ORDER if t in available_tags]
remaining = [t for t in available_tags if t not in preferred_present]
remaining_sorted = sorted(remaining, key=lambda t: size_by_tag[t], reverse=True)
ordered_tags = preferred_present + remaining_sorted

# Helper to build YAML for a given included tag set.

def build_doc_for_tags(included_tags: set[str], include_components: bool) -> tuple[str, int, int]:
    filtered_paths = OrderedDict()
    num_ops = 0
    for path in sorted(all_paths.keys()):
        methods = all_paths[path]
        new_methods = OrderedDict()
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method not in HTTP_METHODS:
                continue
            if not isinstance(op, dict):
                continue
            tags = op.get('tags') or []
            if any(tag in included_tags for tag in tags) or (not tags and "__untagged__" in included_tags):
                new_methods[method] = op
                num_ops += 1
        if new_methods:
            filtered_paths[path] = new_methods

    out = OrderedDict()
    out['openapi'] = data.get('openapi', '3.0.3')
    out['info'] = data.get('info')
    # Avoid servers/tags/security to maximize space for endpoints
    out['paths'] = filtered_paths

    if include_components and 'components' in data:
        # Include only securitySchemes if present; deref spec should have schemas inline
        comps = data['components'] or {}
        comps_out = {}
        if 'securitySchemes' in comps:
            comps_out['securitySchemes'] = comps['securitySchemes']
        if comps_out:
            out['components'] = comps_out

    text = yaml.safe_dump(out, sort_keys=False, width=100000)
    tokens = count_tokens(text)
    return text, tokens, num_ops

included: set[str] = set()
best_text = ""
best_tokens = 0
best_num_ops = 0

# Greedily add tags while staying under the token limit, attempting without components first
for tag in ordered_tags:
    trial = set(included)
    trial.add(tag)
    text, tokens, num_ops = build_doc_for_tags(trial, include_components=False)
    if tokens <= TOKEN_LIMIT:
        included = trial
        best_text = text
        best_tokens = tokens
        best_num_ops = num_ops
    # if too large, skip this tag and continue

# Try to add securitySchemes if there is still space
text_with_comp, tokens_with_comp, _ = build_doc_for_tags(included, include_components=True)
if tokens_with_comp <= TOKEN_LIMIT:
    best_text = text_with_comp
    best_tokens = tokens_with_comp

# Final write
os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write(best_text)

# Report stats
num_lines = sum(1 for _ in open(OUTPUT, 'r', encoding='utf-8'))
print(f"Wrote {OUTPUT}")
print(f"Included tags: {len(included)} -> {sorted(included)}")
print(f"Token count (tiktoken {ENCODING_NAME}): {best_tokens}")
print(f"Line count: {num_lines}")
