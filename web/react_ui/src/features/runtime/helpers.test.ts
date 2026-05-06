import { describe, expect, it } from "vitest";
import { followerRuleNamespace } from "./helpers";
import type { CapabilityMember } from "../../types";

const cap = (name: string): CapabilityMember => ({ name });

describe("followerRuleNamespace", () => {
  it("prefers step_guard when both namespaces are advertised", () => {
    expect(followerRuleNamespace([cap("follower.rules"), cap("step_guard.rules")])).toBe(
      "step_guard"
    );
  });

  it("keeps legacy follower compatibility", () => {
    expect(followerRuleNamespace([cap("follower.rules")])).toBe("follower");
  });

  it("returns null when no rule namespace is advertised", () => {
    expect(followerRuleNamespace([cap("interlock.status")])).toBeNull();
  });
});
