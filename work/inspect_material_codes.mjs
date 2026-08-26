import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import fs from "node:fs/promises";

const inputPath = "D:/project/材质编码.xlsx";
const outputDir = "D:/project/temu-listing/work/material-preview";
await fs.mkdir(outputDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const summary = await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 20000,
  tableMaxRows: 30,
  tableMaxCols: 20,
  tableMaxCellChars: 200,
});
console.log(summary.ndjson);
for (const sheet of workbook.worksheets.items) {
  const used = sheet.getUsedRange(true);
  if (!used) continue;
  const values = used.values;
  console.log(JSON.stringify({ sheet: sheet.name, address: used.address, values }, null, 2));
  const preview = await workbook.render({ sheetName: sheet.name, range: used.address, scale: 2, format: "png" });
  await fs.writeFile(`${outputDir}/${sheet.name.replace(/[\\/:*?"<>|]/g, "_")}.png`, new Uint8Array(await preview.arrayBuffer()));
}
