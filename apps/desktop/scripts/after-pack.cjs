const path = require("node:path");
const rcedit = require("rcedit");

/**
 * Stamp the Windows executable without electron-builder's cross-platform
 * winCodeSign archive. That archive contains macOS symlinks and cannot be
 * extracted by ordinary Windows users when Developer Mode is disabled.
 */
module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== "win32") return;

  const appInfo = context.packager.appInfo;
  const executable = path.join(context.appOutDir, `${appInfo.productFilename}.exe`);
  const icon = await context.packager.getIconPath();
  if (!icon) throw new Error("PaperCreator Windows icon could not be resolved");

  const companyName = appInfo.companyName || "HWSLandDFTX8";
  const copyright = appInfo.copyright || `Copyright © ${new Date().getFullYear()} ${companyName}`;

  await rcedit(executable, {
    icon,
    "file-version": appInfo.version,
    "product-version": `${appInfo.version}.0`,
    "version-string": {
      CompanyName: companyName,
      FileDescription: appInfo.description,
      InternalName: appInfo.productFilename,
      LegalCopyright: copyright,
      OriginalFilename: `${appInfo.productFilename}.exe`,
      ProductName: appInfo.productName,
    },
  });

  process.stdout.write(`Stamped ${path.basename(executable)} with ${path.basename(icon)}\n`);
};
