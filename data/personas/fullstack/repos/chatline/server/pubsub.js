const { createClient } = require('redis');
const sub = createClient();
await sub.subscribe('chat:*', (msg) => io.to(room(msg)).emit('message', JSON.parse(msg)));
