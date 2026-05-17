import Anthropic from "@anthropic-ai/sdk";

let _client: Anthropic | null = null;

export function getAnthropic(): Anthropic {
  if (!_client) {
    _client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
  }
  return _client;
}

export function getModel(): string {
  return process.env.ANTHROPIC_MODEL || "claude-sonnet-4-5";
}

export function getFastModel(): string {
  return process.env.ANTHROPIC_FAST_MODEL || "claude-haiku-4-5";
}
