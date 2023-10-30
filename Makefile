TARGET_ZIP=dist.zip
TARGET_EXCLUDE=".git/*" "venv/*" ".gitignore" "Makefile"

all: clean
	zip -r ${TARGET_ZIP} . -x ${TARGET_EXCLUDE} @

clean:
	@rm -fv ${TARGET_ZIP}