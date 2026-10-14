"""The `query` / `body` dictionary matcher a generated Playwright test carries (BE-0463).

The runner compares every dictionary value against the *text form* of a JSON value
(`bajutsu.common.assertions.dict_match.json_text`): Python's `str` / `repr` for a scalar and compact
`json.dumps` for an object or array. `JSON.parse` cannot reproduce that. It drops the difference
between `1` and `1.0`, and it reorders an object's integer-like keys. So the helper reads the body
with a small JSON reader that keeps key order (a `Map`) and each number's source token. It then
formats numbers the way Python does. `tests/test_codegen_playwright_dict_match.py` runs this source
under `node` against the Python matcher, so the two cannot drift apart unnoticed.

The source keeps its TypeScript annotations to the forms that parity test strips (`: any`,
`: boolean`, `: string`, `: never`, `<string, any>`), so the same text runs as plain JavaScript.
"""

from __future__ import annotations

DICT_MATCH_JS = """\
// query/body dictionaries (BE-0463), mirroring the runner's matcher: each value compares against the
// runner's text form of a JSON value, so the body is read keeping key order and number tokens.
const bajutsuJson = (src: string): any => {
  let i = 0;
  const NUM = /-?(?:0|[1-9]\\d*)(\\.\\d+)?([eE][+-]?\\d+)?/y;
  const STR = /"(?:[^"\\\\\\u0000-\\u001f]|\\\\(?:["\\\\/bfnrt]|u[0-9a-fA-F]{4}))*"/y;
  const fail = (): never => { throw new SyntaxError('not JSON'); };
  const ws = () => { while (i < src.length && ' \\t\\n\\r'.includes(src[i])) i++; };
  const str = (): string => {
    STR.lastIndex = i;
    const m = STR.exec(src);
    if (m === null) return fail();
    i += m[0].length;
    return JSON.parse(m[0]);
  };
  const value = (): any => {
    ws();
    if (src[i] === '{') {
      i++;
      const obj = new Map<string, any>();
      ws();
      if (src[i] === '}') { i++; return obj; }
      for (;;) {
        ws();
        const k = str();
        ws();
        if (src[i++] !== ':') fail();
        obj.set(k, value());
        ws();
        const c = src[i++];
        if (c === '}') return obj;
        if (c !== ',') fail();
      }
    }
    if (src[i] === '[') {
      i++;
      const arr: any = [];
      ws();
      if (src[i] === ']') { i++; return arr; }
      for (;;) {
        arr.push(value());
        ws();
        const c = src[i++];
        if (c === ']') return arr;
        if (c !== ',') fail();
      }
    }
    if (src[i] === '"') return str();
    if (src.startsWith('true', i)) { i += 4; return true; }
    if (src.startsWith('false', i)) { i += 5; return false; }
    if (src.startsWith('null', i)) { i += 4; return null; }
    // Python's reader also takes these three non-standard literals, as floats.
    for (const word of ['NaN', 'Infinity', '-Infinity']) {
      if (src.startsWith(word, i)) { i += word.length; return { num: word, float: true }; }
    }
    NUM.lastIndex = i;
    const m = NUM.exec(src);
    if (m === null) return fail();
    i += m[0].length;
    return { num: m[0], float: m[1] !== undefined || m[2] !== undefined };
  };
  const v = value();
  ws();
  if (i !== src.length) fail();
  return v;
};
// Python's float repr: shortest digits, exponent form below 1e-4 or from 1e16.
const bajutsuFloat = (tok: string, nested: boolean): string => {
  const n = Number(tok);
  if (Number.isNaN(n)) return nested ? 'NaN' : 'nan';
  if (!Number.isFinite(n)) return n > 0 ? (nested ? 'Infinity' : 'inf') : (nested ? '-Infinity' : '-inf');
  if (Object.is(n, -0)) return '-0.0';
  const [mant, ex] = Math.abs(n).toExponential().split('e');
  const d = mant.replace('.', '');
  const e = Number(ex);
  const sign = n < 0 ? '-' : '';
  if (e < -4 || e >= 16) {
    const exp = (e < 0 ? '-' : '+') + String(Math.abs(e)).padStart(2, '0');
    return sign + d[0] + (d.length > 1 ? '.' + d.slice(1) : '') + 'e' + exp;
  }
  if (e < 0) return sign + '0.' + '0'.repeat(-e - 1) + d;
  if (d.length <= e + 1) return sign + d + '0'.repeat(e + 1 - d.length) + '.0';
  return sign + d.slice(0, e + 1) + '.' + d.slice(e + 1);
};
const bajutsuText = (v: any, nested: boolean): string => {
  if (v === true) return 'true';
  if (v === false) return 'false';
  if (v === null) return 'null';
  if (typeof v === 'string') return nested ? JSON.stringify(v) : v;
  if (v instanceof Map) {
    return '{' + [...v].map(([k, x]) => JSON.stringify(k) + ':' + bajutsuText(x, true)).join(',') + '}';
  }
  if (Array.isArray(v)) return '[' + v.map((x) => bajutsuText(x, true)).join(',') + ']';
  return v.float ? bajutsuFloat(v.num, nested) : BigInt(v.num).toString();
};
const bajutsuKey = (k: string): string => (k.startsWith('$$') ? k.slice(1) : k);
const bajutsuIsOps = (w: any): boolean =>
  w !== null && typeof w === 'object' && !Array.isArray(w) && Object.keys(w).length > 0
  && Object.keys(w).every((k) => k.startsWith('$') && !k.startsWith('$$'));
const bajutsuOps = (ops: any, observed: any): boolean => {
  if (ops.$exists === true && observed.length === 0) return false;
  if (ops.$exists === false && observed.length > 0) return false;
  if (ops.$matches === undefined) return true;
  const re = new RegExp(ops.$matches);
  return observed.some((o: any) => re.test(o));
};
const bajutsuBody = (want: any, present: boolean, got: any): boolean => {
  if (bajutsuIsOps(want)) return bajutsuOps(want, present ? [bajutsuText(got, false)] : []);
  if (!present) return false;
  if (typeof want === 'string') return bajutsuText(got, false) === want;
  if (Array.isArray(want)) {
    return Array.isArray(got) && got.length === want.length
      && want.every((w: any, i: any) => bajutsuBody(w, true, got[i]));
  }
  return got instanceof Map
    && Object.keys(want).every((k) => bajutsuBody(want[k], got.has(bajutsuKey(k)), got.get(bajutsuKey(k))));
};
const bajutsuDict = (e: any, query: any, body: any): boolean => {
  if (query !== null) {
    const params = new URL(e.url).searchParams;
    for (const k of Object.keys(query)) {
      const observed = params.getAll(bajutsuKey(k));
      const w = query[k];
      if (typeof w === 'string' ? !observed.includes(w) : !bajutsuOps(w, observed)) return false;
    }
  }
  if (body === null) return true;
  let parsed: any = null;
  try {
    parsed = e.body === null ? null : bajutsuJson(e.body);
  } catch (err) {
    // Not JSON, or nested past the stack: no match, as in the runner. Anything else is a bug.
    if (err instanceof SyntaxError || err instanceof RangeError) return false;
    throw err;
  }
  return parsed instanceof Map && bajutsuBody(body, true, parsed);
};
"""
