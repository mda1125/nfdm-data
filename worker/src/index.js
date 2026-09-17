const ALLOWED_ORIGINS = new Set([
  'https://thedairydesk.com',
  'https://www.thedairydesk.com'
]);

const MAX_MESSAGE_LEN = 500;
const DAILY_LIMIT_PER_IP = 30;

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
  'Rules:',
  '- Only answer questions about dairy/commodity markets, the metrics above, or',
  '  how to read this dashboard.',
  '- If asked something unrelated, say briefly that you only cover topics on',
  '  The Dairy Desk.',
  '- Do not give personalized trading, investment, or booking/hedging advice.',
  '  You may explain what a metric means and how to interpret it in general terms.',
  '- Be concise. The audience already knows dairy market terminology.',
  '- Data on this site uses USDA sources but is not endorsed or certified by USDA.',
  '- You do not have live access to the numbers currently on the page. If asked',
  "  for today's specific value, tell the user to check the relevant chart/tab."
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

    let upstream;
    try {
      upstream = await fetch('https://api.deepseek.com/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + env.DEEPSEEK_API_KEY
        },
        body: JSON.stringify({
          model: 'deepseek-chat',
          messages: [
            { role: 'system', content: SYSTEM_PROMPT },
            { role: 'user', content: message }
          ],
          max_tokens: 400,
          temperature: 0.3
        })
      });
    } catch (err) {
      return Response.json({ error: 'Upstream request failed' }, { status: 502, headers: corsHeaders(origin) });
    }

    if (!upstream.ok) {
      return Response.json({ error: 'AI provider error' }, { status: 502, headers: corsHeaders(origin) });
    }

    const result = await upstream.json();
    const answer = (result && result.choices && result.choices[0] && result.choices[0].message
      && result.choices[0].message.content) || 'No answer returned.';

    return Response.json({ answer: answer }, { headers: corsHeaders(origin) });
  }
};
