import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

for (const inputPath of ["D:/project/工艺代码.xlsx", "D:/project/颜色编码.xlsx"]) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
  const output = [];
  for (const sheet of workbook.worksheets.items) {
    const used = sheet.getUsedRange(true);
    output.push({ sheet: sheet.name, address: used?.address ?? null, values: used?.values ?? [] });
  }
  console.log(JSON.stringify({ inputPath, sheets: output }, null, 2));
}
