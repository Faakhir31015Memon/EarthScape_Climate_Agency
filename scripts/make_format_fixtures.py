"""Rebuild macro-flavoured OOXML fixtures (Excel strips macro parts from macro-free books)."""
import zipfile

D = r"C:\Users\USER\Desktop\EarthScape_Climate_Agency\sample-data\format-fixtures"

SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
XLTM_MAIN = "application/vnd.ms-excel.template.macroEnabled.main+xml"
VBA_PARTS = b"placeholder vbaProject binary (macro stream, never executed by the reader)"


def rebuild(src: str, dst: str, main_type: str) -> None:
    with zipfile.ZipFile(src) as zin:
        items = [(i, zin.read(i.filename)) for i in zin.infolist()]
        rels = zin.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    override = (f'<Override PartName="/xl/workbook.xml" ContentType="{main_type}"/>').encode()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item, data in items:
            if item.filename == "[Content_Types].xml":
                data = data.replace(SHEET.encode(), main_type.encode())
                if b'PartName="/xl/workbook.xml"' not in data:
                    data = data.replace(b"</Types>", override + b"</Types>")
                data = data.replace(b"</Types>",
                                    b'<Override PartName="/xl/vbaProject.bin" ContentType='
                                    b'"application/vnd.ms-office.vbaProject"/></Types>')
            elif item.filename == "xl/_rels/workbook.xml.rels":
                if "vbaProject.bin" not in rels:
                    rels = rels.replace(
                        "</Relationships>",
                        '<Relationship Id="rIdVba" Type="http://schemas.microsoft.com/office/2006/'
                        'relationships/vbaProject" Target="vbaProject.bin"/></Relationships>')
                data = rels.encode("utf-8")
            zout.writestr(item, data)
        zout.writestr("xl/vbaProject.bin", VBA_PARTS)


rebuild(f"{D}/karachi_daily.xlsx", f"{D}/karachi_daily.xlsm", XLSM_MAIN)
rebuild(f"{D}/karachi_daily.xlsx", f"{D}/karachi_template.xltm", XLTM_MAIN)

for name in ("karachi_daily.xlsm", "karachi_template.xltm"):
    with zipfile.ZipFile(f"{D}/{name}") as z:
        ct = z.read("[Content_Types].xml").decode()
        print(f"{name}: vba={('xl/vbaProject.bin' in z.namelist())} "
              f"main={'macroEnabled.main' in ct} templateMain={'template.macroEnabled' in ct}")
