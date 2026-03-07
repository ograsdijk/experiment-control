export type YamlColorTokenKind =
  | "key"
  | "string"
  | "number"
  | "bool"
  | "template"
  | "comment";

export function yamlTokenColor(
  kind: YamlColorTokenKind,
  colorScheme: "light" | "dark"
): string {
  const isDark = colorScheme === "dark";
  if (kind === "key") {
    return isDark ? "#74c0fc" : "#1c7ed6";
  }
  if (kind === "string") {
    return isDark ? "#8ce99a" : "#2b8a3e";
  }
  if (kind === "number") {
    return isDark ? "#ffa94d" : "#d9480f";
  }
  if (kind === "bool") {
    return isDark ? "#b197fc" : "#5f3dc4";
  }
  if (kind === "template") {
    return isDark ? "#faa2c1" : "#c2255c";
  }
  return isDark ? "#adb5bd" : "#868e96";
}

