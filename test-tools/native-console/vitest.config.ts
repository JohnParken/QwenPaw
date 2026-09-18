// Deliberately no Vite 5 plugin/config imports: Vitest 0.32 owns its Vite 4 dependency.
export default {
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "jsdom",
    threads: false,
  },
};
