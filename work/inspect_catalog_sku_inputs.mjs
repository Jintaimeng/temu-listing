import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load("D:/project/20260803_Temu批量上架_最终版_clean.xlsx"));
for (const sheet of workbook.worksheets.items) {
  const used = sheet.getUsedRange(true);
  if (!used) continue;
  const values = used.values;
  console.log(JSON.stringify({ sheet: sheet.name, address: used.address, headers: values.slice(0, 2), rows: values.slice(0, 6) }, null, 2));
}
