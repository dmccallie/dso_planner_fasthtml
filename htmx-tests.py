# test some htmx stuff with fasthtml

from fasthtml.common import *

app, rt = fast_app()

global count
count = 0

def filter_bar(text: str, oob=False) -> FT:
    attrs = {"id": "filter-bar"}
    if oob:
        attrs["hx-swap-oob"] = "true"
    return Div(text, **attrs)

def main_table() -> FT:
    return Table(
        Thead(Tr(Th("Name"), Th("Score"))),
        Tbody(
            Tr(Td("Alice"), Td("100")),
            Tr(Td("Bob"), Td("200"))
        ),
        id="main-table"
    )

@app.get("/")
def index():
    # Initial page: show both inline
    return Html(
        Head(),
        Body(
            filter_bar(),
            main_table()
        )
    )

@app.get("/test-fragment")
def test_fragment():
    # Explicit concatenation (always safe)
    # NO NO does not work!
    return "".join(str(x) for x in (main_table(), filter_bar(oob=True).to_xml()))

@app.get("/test-tuple")
def test_tuple():
    global count
    # Return a raw tuple — will this serialize correctly?
    # YES, works
    tab_xml = to_xml(main_table())
    fil_xml = to_xml(filter_bar("init load", oob=True))
    count += 1
    print("test_tuple called - ", tab_xml, fil_xml)
    return main_table(), filter_bar(f"count = {count}", oob=True)

serve()

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="127.0.0.1", port=8000)
