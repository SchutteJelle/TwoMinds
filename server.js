import 'dotenv/config';
import express from 'express';
import Anthropic from '@anthropic-ai/sdk';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { randomUUID } from 'crypto';
import { existsSync } from 'fs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = join(__dirname, '.env');
const hasDotEnvFile = existsSync(envPath);
const app = express();
const client = new Anthropic();
const hasAnthropicApiKey = Boolean(process.env.ANTHROPIC_API_KEY);
const sessions = new Map();

app.use(express.json());
app.use(express.static(join(__dirname, 'public')));

app.get('/api/health', (_req, res) => {
  res.json({
    ok: true,
    configured: hasAnthropicApiKey,
  });
});

app.post('/api/converse/:sessionId/user-turn', (req, res) => {
  const session = sessions.get(req.params.sessionId);
  if (!session || !session.active) {
    return res.status(404).json({ error: 'Conversation session not found or no longer active.' });
  }

  const text = typeof req.body?.text === 'string' ? req.body.text.trim() : '';
  if (!text) {
    return res.status(400).json({ error: 'text is required' });
  }

  session.userQueue.push(text.slice(0, 2000));
  return res.json({ ok: true, queued: session.userQueue.length });
});

/**
 * Build the messages array from a given agent's perspective.
 * The current agent = assistant, the other agent = user.
 *
 * Agent 1 (a1) always opens the discussion, so their first-turn messages
 * array contains a synthetic "user" kickoff prompt.
 * Agent 2 (a2) first sees Agent 1's opening message as a user message.
 */
function buildMessages(speakerKey, history) {
  const messages = [];

  if (speakerKey === 'a1') {
    // Kickoff prompt so Agent 1 has a user message to respond to
    messages.push({
      role: 'user',
      content: 'Please begin the discussion with your opening thoughts.',
    });
    for (const entry of history) {
      messages.push({
        role: entry.speaker === 'a1' ? 'assistant' : 'user',
        content: entry.text,
      });
    }
  } else {
    // Agent 2: Agent 1's turns are user messages, Agent 2's are assistant
    for (const entry of history) {
      messages.push({
        role: entry.speaker === 'a2' ? 'assistant' : 'user',
        content: entry.text,
      });
    }
  }

  return messages;
}

app.post('/api/converse', async (req, res) => {
  if (!hasAnthropicApiKey) {
    return res.status(503).json({
      error: 'Missing ANTHROPIC_API_KEY. Set the environment variable and restart the server.',
    });
  }

  const { agent1, agent2, topic } = req.body;
  const requestedTurns = Number.parseInt(req.body.turns, 10);
  const turns = Number.isFinite(requestedTurns)
    ? Math.min(12, Math.max(2, requestedTurns))
    : 6;

  if (!agent1?.name || !agent2?.name || typeof topic !== 'string' || !topic.trim()) {
    return res.status(400).json({ error: 'agent1, agent2, and topic are required' });
  }

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  const send = (data) => {
    if (!res.writableEnded) {
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    }
  };

  const sessionId = randomUUID();
  const session = { active: true, userQueue: [] };
  sessions.set(sessionId, session);

  const history = []; // { speaker: 'a1'|'a2'|'human', text: string }
  let aborted = false;
  req.on('close', () => {
    aborted = true;
    session.active = false;
    sessions.delete(sessionId);
  });

  try {
    send({
      type: 'start',
      sessionId,
      agent1Name: agent1.name,
      agent2Name: agent2.name,
      topic,
    });

    for (let i = 0; i < turns; i++) {
      if (aborted) break;

      // User messages are queued via a separate endpoint and inserted before the next AI turn.
      if (session.userQueue.length > 0) {
        const queuedMessages = session.userQueue.splice(0);
        for (const userText of queuedMessages) {
          history.push({ speaker: 'human', text: userText });
          send({ type: 'user_injected', name: 'You', text: userText });
        }
      }

      const isA1 = i % 2 === 0;
      const current = isA1 ? agent1 : agent2;
      const other = isA1 ? agent2 : agent1;
      const speakerKey = isA1 ? 'a1' : 'a2';

      const personality = typeof current.personality === 'string' && current.personality.trim()
        ? current.personality.trim()
        : 'You are thoughtful, concise, and respectful.';

      const system = `You are ${current.name}. ${personality}

You are engaged in an intellectual dialogue with ${other.name} on this topic: "${topic}"

Guidelines:
- Respond directly to what was just said
- Be conversational and genuine — stay fully in character
- Keep your response to 2-3 focused paragraphs of flowing prose
- No bullet points, numbered lists, or headers`;

      send({ type: 'turn_start', speaker: speakerKey, name: current.name });

      let text = '';

      const stream = client.messages.stream({
        model: 'claude-opus-4-6',
        max_tokens: 1024,
        system,
        messages: buildMessages(speakerKey, history),
      });

      for await (const event of stream) {
        if (aborted) break;
        if (
          event.type === 'content_block_delta' &&
          event.delta.type === 'text_delta'
        ) {
          text += event.delta.text;
          send({ type: 'token', speaker: speakerKey, text: event.delta.text });
        }
      }

      if (!aborted && text) {
        history.push({ speaker: speakerKey, text });
        send({ type: 'turn_end', speaker: speakerKey });
      }
    }

    if (!aborted) send({ type: 'done' });
  } catch (err) {
    console.error('Stream error:', err.message);
    send({ type: 'error', message: err.message });
  } finally {
    session.active = false;
    sessions.delete(sessionId);
    res.end();
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Startup: .env file ${hasDotEnvFile ? `found at ${envPath}` : 'not found (using shell env only)'}.`);
  console.log(`Startup: ANTHROPIC_API_KEY ${hasAnthropicApiKey ? 'loaded' : 'missing'}.`);

  if (!hasAnthropicApiKey) {
    console.warn('Warning: ANTHROPIC_API_KEY is missing. /api/converse will return 503 until configured.');
  }
  console.log(`\nTwoMinds running at http://localhost:${PORT}\n`);
});
