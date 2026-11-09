import esphome.config_validation as cv

# Empty schema: the `blackout:` key only has to exist so ESPHome loads the component
# and exposes its headers to lambdas. No configuration is required.
CONFIG_SCHEMA = cv.Schema({})