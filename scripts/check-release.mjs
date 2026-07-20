import { access, readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const errors = [];

const readJson = async (path) =>
  JSON.parse(await readFile(resolve(root, path), "utf8"));
const readTomlField = async (path, field) => {
  const content = await readFile(resolve(root, path), "utf8");
  const match = content.match(new RegExp(`^${field}\\s*=\\s*"([^"]+)"`, "m"));
  return match?.[1];
};

const packageJson = await readJson("package.json");
const packageLock = await readJson("package-lock.json");
const tauriConfig = await readJson("src-tauri/tauri.conf.json");
const openapi = await readJson("openapi.json");
const defaultCapability = await readJson("src-tauri/capabilities/default.json");
const platformTauriConfigs = await Promise.all(
  [
    "src-tauri/tauri.linux.conf.json",
    "src-tauri/tauri.macos.conf.json",
    "src-tauri/tauri.windows.conf.json",
  ].map(readJson),
);

const versions = new Map([
  ["package.json", packageJson.version],
  ["package-lock.json", packageLock.version],
  ["src-tauri/tauri.conf.json", tauriConfig.version],
  [
    "src-tauri/Cargo.toml",
    await readTomlField("src-tauri/Cargo.toml", "version"),
  ],
  [
    "python/pyproject.toml",
    await readTomlField("python/pyproject.toml", "version"),
  ],
  ["openapi.json", openapi.info?.version],
]);

const expectedVersion = packageJson.version;
for (const [path, version] of versions) {
  if (version !== expectedVersion)
    errors.push(
      `${path}: expected version ${expectedVersion}, found ${version ?? "missing"}`,
    );
}
const expectedLicense = "AGPL-3.0-only";
const licenses = new Map([
  ["package.json", packageJson.license],
  ["package-lock.json", packageLock.packages?.[""]?.license],
  [
    "src-tauri/Cargo.toml",
    await readTomlField("src-tauri/Cargo.toml", "license"),
  ],
  [
    "python/pyproject.toml",
    await readTomlField("python/pyproject.toml", "license"),
  ],
]);
for (const [path, license] of licenses) {
  if (license !== expectedLicense) {
    errors.push(
      `${path}: expected license ${expectedLicense}, found ${license ?? "missing"}`,
    );
  }
}

if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(expectedVersion)) {
  errors.push(
    `package.json: ${expectedVersion} is not a supported release version`,
  );
}

const tagIndex = process.argv.indexOf("--tag");
const suppliedTag = tagIndex >= 0 ? process.argv[tagIndex + 1] : undefined;
const releaseTag =
  suppliedTag ??
  (process.env.GITHUB_REF_TYPE === "tag"
    ? process.env.GITHUB_REF_NAME
    : undefined);
if (tagIndex >= 0 && !suppliedTag) errors.push("--tag requires a value");
if (releaseTag && releaseTag !== `v${expectedVersion}`) {
  errors.push(`release tag ${releaseTag} does not match v${expectedVersion}`);
}

if (tauriConfig.productName !== "Meeting Supporter")
  errors.push("Tauri productName must be Meeting Supporter");
if (tauriConfig.bundle?.active !== true)
  errors.push("Tauri bundling must be active");
const bundleLicenseFile = tauriConfig.bundle?.licenseFile;
if (bundleLicenseFile !== "../LICENSE") {
  errors.push("Tauri bundle licenseFile must be ../LICENSE");
} else {
  try {
    const bundleLicense = await readFile(
      resolve(root, "src-tauri", bundleLicenseFile),
      "utf8",
    );
    if (
      !bundleLicense.startsWith(
        "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007",
      ) ||
      !bundleLicense.includes("END OF TERMS AND CONDITIONS")
    ) {
      errors.push("Tauri bundle LICENSE must contain the complete AGPLv3 text");
    }
  } catch {
    errors.push("Tauri bundle LICENSE could not be read");
  }
}
if (
  tauriConfig.bundle?.resources?.["../THIRD-PARTY-NOTICES.txt"] !==
  "THIRD-PARTY-NOTICES.txt"
) {
  errors.push("Tauri bundle must include THIRD-PARTY-NOTICES.txt");
}
for (const config of [tauriConfig, ...platformTauriConfigs]) {
  const resources = Object.keys(config.bundle?.resources ?? {});
  if (
    resources.some((resource) => /resources\/uv(?:\/|\.exe$|$)/.test(resource))
  ) {
    errors.push("Tauri bundle must not redistribute the provisioned uv binary");
  }
}

const productionPermissions = defaultCapability.permissions ?? [];
const webdriverPermissions = productionPermissions.filter((permission) =>
  permission.startsWith("wdio"),
);
if (webdriverPermissions.length > 0) {
  errors.push(
    `production capability contains WebDriver permissions: ${webdriverPermissions.join(", ")}`,
  );
}

for (const path of [
  "LICENSE",
  "THIRD-PARTY-NOTICES.txt",
  "public/favicon.svg",
  "src-tauri/icons/icon.icns",
  "src-tauri/icons/icon.ico",
]) {
  try {
    await access(resolve(root, path));
  } catch {
    errors.push(`${path}: required release asset is missing`);
  }
}

if (errors.length > 0) {
  console.error("Release preflight failed:");
  for (const error of errors) console.error(`- ${error}`);
  process.exitCode = 1;
} else {
  console.log(
    `Release preflight passed for Meeting Supporter v${expectedVersion}`,
  );
}
