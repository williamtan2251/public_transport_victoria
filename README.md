The `public transport victoria` sensor platform uses the [Public Transport Victoria (PTV)](https://www.ptv.vic.gov.au/) as a source for public transport departure times for Victoria, Australia.

## Thanks
This is a branch of the original PTV integration by Bremor.

## Installation (There are two methods, with HACS or manual)

Install via HACS (add this as a custom integration) or install manually by copying the files in a new 'custom_components/public_transport_victoria' directory.

## Prerequisites

### Developer ID and API Key
Please follow the instructions on http://ptv.vic.gov.au/ptv-timetable-api/ for obtaining a Developer ID and API Key.

## Configuration
After you have installed the custom component (see above):
1. Goto the `Configuration` -> `Integrations` page.  
2. On the bottom right of the page, click on the `+ Add Integration` sign to add an integration.
3. Search for `Public Transport Victoria`. (If you don't see it, try refreshing your browser page to reload the cache.)
4. Click `Submit` to add the integration.

## Notes
This integration refreshes data every minute. If you previously used an automation like the one below to force more frequent updates during peak periods, it is no longer required.
```
automation:

  - alias: 'update_trains'
    initial_state: true
    trigger:
      trigger:
      - platform: time_pattern
        minutes: "/1"
    condition:
      condition: or
      conditions:
        - condition: time
          after: '07:30:00'
          before: '08:30:00'
        - condition: time
          after: '16:45:00'
          before: '17:45:00'
    action:
      - service: 'homeassistant.update_entity'
        data:
          entity_id:
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_0'
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_1'
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_2'
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_3'
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_4'
```
