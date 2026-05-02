<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.5-Prizren" styleCategories="Symbology|Labeling">
  <renderer-v2 type="categorizedSymbol" attr="building_type">
    <symbols>
      <symbol name="0" type="fill" alpha="1">
        <layer class="SimpleFill" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="252,141,98,180"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.26"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="1" type="fill" alpha="1">
        <layer class="SimpleFill" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="102,194,165,180"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.26"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="2" type="fill" alpha="1">
        <layer class="SimpleFill" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="200,200,200,150"/>
            <Option name="outline_color" type="QString" value="100,100,100,255"/>
            <Option name="outline_width" type="QString" value="0.16"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
    <categories>
      <category value="house" label="House" symbol="0" render="true"/>
      <category value="apartment" label="Apartment" symbol="1" render="true"/>
      <category value="" label="All other values" symbol="2" render="true"/>
    </categories>
  </renderer-v2>
  <labeling type="simple">
    <settings>
      <text-style fontFamily="Helvetica" fontSize="9" fontWeight="50" textColor="20,20,20,255">
        <text-buffer bufferDraw="1" bufferSize="1.0" bufferColor="255,255,255,230"/>
      </text-style>
      <placement placement="0"/>
      <fieldName>addr_housenumber</fieldName>
    </settings>
  </labeling>
</qgis>
