import { create } from "zustand";
import type { AppConfig, ProviderList, Agent, Command, Skill } from "../types";
import { MimoClient } from "../api/mimoClient";

interface ConfigState {
  /* Config */
  config: AppConfig | null;
  configLoading: boolean;

  /* Providers */
  providers: ProviderList | null;
  providersLoading: boolean;

  /* Agents */
  agents: Agent[];
  agentsLoading: boolean;
  currentAgent: string;

  /* Commands */
  commands: Command[];
  commandsLoading: boolean;

  /* Skills */
  skills: Skill[];
  skillsLoading: boolean;

  /* Actions */
  loadConfig: () => Promise<void>;
  updateConfig: (partial: Partial<AppConfig>) => Promise<void>;
  loadProviders: () => Promise<void>;
  loadAgents: () => Promise<void>;
  loadCommands: () => Promise<void>;
  loadSkills: () => Promise<void>;
  loadAll: () => Promise<void>;
  setCurrentAgent: (agent: string) => void;
}

export const useConfigStore = create<ConfigState>((set) => ({
  config: null,
  configLoading: false,

  providers: null,
  providersLoading: false,

  agents: [],
  agentsLoading: false,
  currentAgent: "build",

  commands: [],
  commandsLoading: false,

  skills: [],
  skillsLoading: false,

  loadConfig: async () => {
    set({ configLoading: true });
    try {
      const config = await MimoClient.getConfig();
      set({ config, configLoading: false });
    } catch {
      set({ configLoading: false });
    }
  },

  updateConfig: async (partial: Partial<AppConfig>) => {
    try {
      const updated = await MimoClient.updateConfig(partial);
      set({ config: updated });
    } catch {
      // silently fail
    }
  },

  loadProviders: async () => {
    set({ providersLoading: true });
    try {
      const providers = await MimoClient.getProviders();
      set({ providers, providersLoading: false });
    } catch {
      set({ providersLoading: false });
    }
  },

  loadAgents: async () => {
    set({ agentsLoading: true });
    try {
      const agents = await MimoClient.listAgents();
      set({ agents, agentsLoading: false });
    } catch {
      set({ agentsLoading: false });
    }
  },

  loadCommands: async () => {
    set({ commandsLoading: true });
    try {
      const commands = await MimoClient.listCommands();
      set({ commands, commandsLoading: false });
    } catch {
      set({ commandsLoading: false });
    }
  },

  loadSkills: async () => {
    set({ skillsLoading: true });
    try {
      const skills = await MimoClient.listSkills();
      set({ skills, skillsLoading: false });
    } catch {
      set({ skillsLoading: false });
    }
  },

  loadAll: async () => {
    // Fire all loads in parallel
    await Promise.allSettled([
      set({ configLoading: true }),
      set({ providersLoading: true }),
      set({ agentsLoading: true }),
      set({ commandsLoading: true }),
      set({ skillsLoading: true }),
    ]);

    const results = await Promise.allSettled([
      MimoClient.getConfig(),
      MimoClient.getProviders(),
      MimoClient.listAgents(),
      MimoClient.listCommands(),
      MimoClient.listSkills(),
    ]);

    // Config
    if (results[0].status === "fulfilled") {
      set({ config: results[0].value, configLoading: false });
    } else {
      set({ configLoading: false });
    }

    // Providers
    if (results[1].status === "fulfilled") {
      set({ providers: results[1].value, providersLoading: false });
    } else {
      set({ providersLoading: false });
    }

    // Agents
    if (results[2].status === "fulfilled") {
      set({ agents: results[2].value, agentsLoading: false });
    } else {
      set({ agentsLoading: false });
    }

    // Commands
    if (results[3].status === "fulfilled") {
      set({ commands: results[3].value, commandsLoading: false });
    } else {
      set({ commandsLoading: false });
    }

    // Skills
    if (results[4].status === "fulfilled") {
      set({ skills: results[4].value, skillsLoading: false });
    } else {
      set({ skillsLoading: false });
    }
  },

  setCurrentAgent: (agent: string) => {
    set({ currentAgent: agent });
  },
}));
