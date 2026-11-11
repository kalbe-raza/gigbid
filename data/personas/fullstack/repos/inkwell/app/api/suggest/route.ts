export async function POST(req: Request) {
  const stream = await openai.chat.completions.create({ model, messages, stream: true });
  return new Response(toReadable(stream));
}
