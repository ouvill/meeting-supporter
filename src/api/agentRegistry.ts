import { z } from "zod";
import { client } from "./generated/client.gen";

const statusSchema = z.object({
  ready: z.boolean(),
  message: z.string(),
  auth_methods: z.array(z.object({ id: z.string(), name: z.string() })),
});
const agentSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  authors: z.array(z.string()),
  version: z.string(),
  installed_version: z.string().nullable(),
  update_version: z.string().nullable(),
  supported: z.boolean(),
  distribution: z.enum(["binary", "npm", "unsupported"]),
  status: statusSchema,
});
const catalogSchema = z.object({
  supported: z.literal(true),
  agents: z.array(agentSchema),
  update_count: z.number().int().nonnegative(),
  checked_at: z.number().nullable(),
  update_message: z.string().nullable(),
});
export type RegistryAgent = z.infer<typeof agentSchema>;
export type AgentCatalog = z.infer<typeof catalogSchema>;

function parse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (!result.success)
    throw new Error("エージェントの応答を読み取れませんでした。");
  return result.data;
}

function failure(error: unknown): Error {
  const parsed = z.object({ detail: z.string() }).safeParse(error);
  return new Error(
    parsed.success
      ? parsed.data.detail
      : "エージェントの操作に失敗しました。再度お試しください。",
  );
}

// The Registry and installations are owned by the Rust desktop runtime.
// The Python backend does not expose these operations.
export async function getAgentCatalog(
  refresh = false,
  signal?: AbortSignal,
): Promise<AgentCatalog | null> {
  const { data, error, response } = await client.get({
    url: "/api/ai/agents",
    query: { refresh },
    signal,
  });
  if (response?.status === 404 || response?.status === 501) return null;
  if (error) throw failure(error);
  return parse(catalogSchema, data);
}
export async function installAgent(id: string) {
  const { data, error } = await client.post({
    url: `/api/ai/agents/${encodeURIComponent(id)}/install`,
  });
  if (error) throw failure(error);
  parse(z.object({ ok: z.literal(true) }), data);
}
export async function updateAllAgents() {
  const { data, error } = await client.post({
    url: "/api/ai/agents/update-all",
  });
  if (error) throw failure(error);
  return parse(
    z.object({
      results: z.array(
        z.object({
          id: z.string(),
          name: z.string(),
          updated: z.boolean(),
          error: z.string().nullable(),
        }),
      ),
    }),
    data,
  );
}
export async function connectAgent(id: string, method?: string) {
  const { data, error } = await client.post({
    url: `/api/ai/agents/${encodeURIComponent(id)}/connect`,
    headers: { "Content-Type": "application/json" },
    body: method ? { method } : {},
  });
  if (error) throw failure(error);
  return parse(statusSchema, data);
}
export async function removeAgent(id: string) {
  const { data, error } = await client.delete({
    url: `/api/ai/agents/${encodeURIComponent(id)}`,
  });
  if (error) throw failure(error);
  parse(z.object({ ok: z.literal(true) }), data);
}
