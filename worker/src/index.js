const ALLOWED_ORIGINS = new Set([
  'https://thedairydesk.com',
  'https://www.thedairydesk.com'
]);

const MAX_MESSAGE_LEN = 500;
const DAILY_LIMIT_PER_IP = 30;

const DATA_BASE = 'https://thedairydesk.com/data/';
const SNAPSHOT_CACHE_KEY = 'market-snapshot:v1';
const SNAPSHOT_TTL_SECONDS = 600;

const SYSTEM_PROMPT = [
  'You are the assistant embedded in The Dairy Desk (thedairydesk.com), a dairy',
  'ingredient market intelligence dashboard.',
  '',
  'Scope: NFDM (nonfat dry milk) CME spot vs the USDA NDPSR survey and the basis',
  'between them, FMMO Class IV, the NFDM futures curve, milk-protein estimates,',
  'seasonality, supply fundamentals (production and stocks), NFDM/SMP export',
  'markets by destination, sugar #11, cocoa, and whey protein (WPC34/WPC80/WPI)',
  'indications.',
  '',
  'You will be given a "Current market snapshot" system message with live-ish',
  'numbers pulled from this site\'s data feeds (may lag the live page by up to',
  '~10 minutes). Use it to answer questions with real numbers and to reason',
  'about likely drivers behind a move (e.g. "why is X up") by connecting related',
  'series in the snapshot -- this is expected and encouraged, not just',
  'definition-lookup. Be upfront that this is your read of the available data,',
  'not a certainty, especially for causal claims.',
  '',
  'Rules:',
  '- Only answer questions about dairy/commodity markets, the metrics above, or',
  '  how to read this dashboard.',
  '- If asked something unrelated, say briefly that you only cover topics on',
  '  The Dairy Desk.',
  '- Do not give personalized trading, investment, or booking/hedging advice',
  '  (e.g. do not tell someone to buy/sell/book at a specific time). General',
  '  market explanation and interpretation is fine and encouraged.',
  '- If a metric is not in the snapshot or you are unsure, say so plainly rather',
  '  than inventing a number -- point to the relevant chart/tab instead.',
  '- Be concise. The audience already knows dairy market terminology.',
  '- Data on this site uses USDA sources but is not endorsed or certified by USDA.'
].join('\n');

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
    'Content-Type': 'application/json',
    'Vary': 'Origin'
  };
}

async function checkRateLimit(env, ip) {
  const day = new Date().toISOString().slice(0, 10);
  const key = 'rl:' + ip + ':' + day;
  const current = parseInt((await env.RATE_LIMIT.get(key)) || '0', 10);
  if (current >= DAILY_LIMIT_PER_IP) return false;
  await env.RATE_LIMIT.put(key, String(current + 1), { expirationTtl: 172800 });
  return true;
}

function pctChange(from, to) {
  if (from === undefined || from === null || from === 0) return null;
  return (((to - from) / from) * 100).toFixed(1);
}

function fmtPct(chg) {
  return chg === null ? '' : ' (' + (chg >= 0 ? '+' : '') + chg + '%)';
}

async function fetchJson(path) {
  const res = await fetch(DATA_BASE + path + '?cb=' + Date.now());
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}

async function snapshotLine(label, path, render) {
  try {
    const json = await fetchJson(path);
    return render(json);
  } catch (err) {
    return label + ': unavailable (' + (err && err.message) + ')';
  }
}

async function buildMarketSnapshot() {
  const lines = await Promise.all([
    snapshotLine('NFDM CME spot', 'cme.json', (d) => {
      const rows = d.data;
      const last = rows[rows.length - 1];
      const prior = rows[Math.max(0, rows.length - 6)];
      return 'NFDM CME spot: $' + last.price + '/lb on ' + last.date +
        ', vs $' + prior.price + ' on ' + prior.date + fmtPct(pctChange(prior.price, last.price));
    }),
    snapshotLine('NDPSR survey', 'nass.json', (d) => {
      const rows = d.data;
      const last = rows[rows.length - 1];
      const prior = rows[rows.length - 2];
      return 'NDPSR (USDA survey) NFDM: $' + last.price + '/lb, week of ' + last.date +
        (prior ? ' vs $' + prior.price + ' prior week' + fmtPct(pctChange(prior.price, last.price)) : '');
    }),
    snapshotLine('FMMO Class IV', 'class_iv.json', (d) => {
      const rows = d.data;
      const last = rows[rows.length - 1];
      const prior = rows[rows.length - 2];
      return 'FMMO Class IV: announced $' + last.announced + '/cwt for ' + last.month + ' ' + last.year +
        ' (implied $' + last.implied + '/cwt)' +
        (prior ? ', vs $' + prior.announced + ' prior month' + fmtPct(pctChange(prior.announced, last.announced)) : '');
    }),
    snapshotLine('Whey indications', 'whey.json', (d) => {
      return 'Whey protein indications (USDA DMN, mid $/lb, week-over-week %):\n' +
        d.data.map(function (p) {
          return '  ' + p.code + ': $' + p.mid + ' (' + (p.wow_pct >= 0 ? '+' : '') + p.wow_pct + '%, ' + p.status + ')';
        }).join('\n');
    }),
    snapshotLine('WPC80 market call', 'whey_market_call.json', (d) => {
      const l = d.latest;
      return 'WPC80 forward call (' + l.target_label + ', analyst estimate, not a quote): blended $' +
        l.blended + '/lb (range $' + l.range.low + '-$' + l.range.high + '). Note: ' + l.qualitative.note;
    }),
    snapshotLine('Sugar #11', 'sugar.json', (d) => {
      const rows = d.data;
      const last = rows[rows.length - 1];
      const prior = rows[Math.max(0, rows.length - 6)];
      return 'Sugar #11: ' + last.price_cents_lb + 'c/lb on ' + last.date +
        ', vs ' + prior.price_cents_lb + 'c on ' + prior.date + fmtPct(pctChange(prior.price_cents_lb, last.price_cents_lb));
    }),
    snapshotLine('Cocoa', 'cocoa.json', (d) => {
      const rows = d.data;
      const last = rows[rows.length - 1];
      const prior = rows[Math.max(0, rows.length - 6)];
      return 'Cocoa: $' + last.price_usd_mt + '/MT on ' + last.date +
        ', vs $' + prior.price_usd_mt + ' on ' + prior.date + fmtPct(pctChange(prior.price_usd_mt, last.price_usd_mt));
    }),
    snapshotLine('NFDM futures curve', 'futures.json', (d) => {
      const rows = d.data.slice(0, 5);
      return 'NFDM futures curve (front months, $/lb settle): ' +
        rows.map(function (r) { return r.label + ' $' + r.settle; }).join(', ');
    })
  ]);
  return lines.join('\n');
}

async function getMarketSnapshot(env) {
  const cached = await env.RATE_LIMIT.get(SNAPSHOT_CACHE_KEY);
  if (cached) return cached;
  const fresh = await buildMarketSnapshot();
  await env.RATE_LIMIT.put(SNAPSHOT_CACHE_KEY, fresh, { expirationTtl: SNAPSHOT_TTL_SECONDS });
  return fresh;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const allowed = ALLOWED_ORIGINS.has(origin);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: allowed ? corsHeaders(origin) : {} });
    }

    if (!allowed) {
      return Response.json({ error: 'Origin not allowed' }, { status: 403 });
    }

    const url = new URL(request.url);
    if (url.pathname !== '/chat' || request.method !== 'POST') {
      return Response.json({ error: 'Not found' }, { status: 404, headers: corsHeaders(origin) });
    }

    const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
    const withinLimit = await checkRateLimit(env, ip);
    if (!withinLimit) {
      return Response.json(
        { error: 'Daily question limit reached. Try again tomorrow.' },
        { status: 429, headers: corsHeaders(origin) }
      );
    }

    let body;
    try {
      body = await request.json();
    } catch (err) {
      return Response.json({ error: 'Invalid JSON' }, { status: 400, headers: corsHeaders(origin) });
    }

    const message = typeof body.message === 'string' ? body.message.trim() : '';
    if (!message || message.length > MAX_MESSAGE_LEN) {
      return Response.json(
        { error: 'Message must be 1-' + MAX_MESSAGE_LEN + ' characters' },
        { status: 400, headers: corsHeaders(origin) }
      );
    }

    let snapshot;
    try {
      snapshot = await getMarketSnapshot(env);
    } catch (err) {
      console.log('Snapshot build failed:', err && err.message);
      snapshot = '(market snapshot unavailable right now)';
    }

    let upstream;
    try {
      upstream = await fetch('https://api.deepseek.com/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + env.DEEPSEEK_API_KEY
        },
        body: JSON.stringify({
          model: 'deepseek-flash',
          thinking: { type: 'disabled' },
          messages: [
            { role: 'system', content: SYSTEM_PROMPT },
            { role: 'system', content: 'Current market snapshot (fetched live, may lag a few minutes):\n' + snapshot },
            { role: 'user', content: message }
          ],
          max_tokens: 900,
          temperature: 0.3
        })
      });
    } catch (err) {
      console.log('DeepSeek fetch threw:', err && err.message);
      return Response.json({ error: 'Upstream request failed' }, { status: 502, headers: corsHeaders(origin) });
    }

    if (!upstream.ok) {
      const bodyText = await upstream.text();
      console.log('DeepSeek returned', upstream.status, bodyText);
      return Response.json({ error: 'AI provider error' }, { status: 502, headers: corsHeaders(origin) });
    }

    const result = await upstream.json();
    const answer = (result && result.choices && result.choices[0] && result.choices[0].message
      && result.choices[0].message.content) || 'No answer returned.';
    if (answer === 'No answer returned.') {
      console.log('DeepSeek 200 with no usable content:', JSON.stringify(result));
    }

    return Response.json({ answer: answer }, { headers: corsHeaders(origin) });
  }
};
