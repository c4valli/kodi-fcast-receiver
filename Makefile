
TARGET_ZIP=dist.zip
TARGET_DIR=$(shell basename `pwd`)
TARGET_EXCLUDE="${TARGET_DIR}/.git/*" "${TARGET_DIR}/venv/*" "${TARGET_DIR}/.gitignore" "${TARGET_DIR}/Makefile" "${TARGET_DIR}/__pycache__"

all: clean
	@cd .. && zip -r ${TARGET_DIR}/${TARGET_ZIP} ${TARGET_DIR} -x ${TARGET_EXCLUDE} @

clean:
	@rm -fv ${TARGET_ZIP}