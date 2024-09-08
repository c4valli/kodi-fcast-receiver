NAME=kodi-fcast-receiver
VERSION=0.0.1
SOURCE_DIR=$(shell basename `pwd`)
TARGET_DIR=$(shell basename `pwd`)/dist
TARGET_ZIP=${NAME}-${VERSION}.zip
SOURCE_EXCLUDE="${SOURCE_DIR}/.git/*" "${SOURCE_DIR}/venv/*" "${SOURCE_DIR}/.gitignore" "${SOURCE_DIR}/Makefile" "${SOURCE_DIR}/__pycache__" "${TARGET_DIR}"

all: clean
	@mkdir -p ${TARGET_DIR}
	@cd .. && zip -r ${TARGET_DIR}/${TARGET_ZIP} ${SOURCE_DIR} -x ${SOURCE_EXCLUDE} @

clean:
	@rm -fv ${TARGET_DIR}/*
