<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.5-Prizren" styleCategories="Symbology">
  <renderer-v2 type="categorizedSymbol" attr="landuse" forceraster="0" symbollevels="0" enableorderby="0">
    <symbols>
      <symbol name="0" type="fill" alpha="1" force_rhr="0" clip_to_extent="1">
        <layer class="SimpleFill" enabled="1" pass="0" locked="0">
          <Option type="Map">
            <Option name="color" type="QString" value="232,167,99,200"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.26"/>
            <Option name="style" type="QString" value="solid"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="1" type="fill" alpha="1">
        <layer class="SimpleFill" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="166,206,227,200"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.26"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="2" type="fill" alpha="1">
        <layer class="SimpleFill" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="178,223,138,200"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.26"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="3" type="fill" alpha="1">
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
      <category value="residential" label="Residential" symbol="0" render="true"/>
      <category value="commercial" label="Commercial" symbol="1" render="true"/>
      <category value="forest" label="Forest" symbol="2" render="true"/>
      <category value="" label="All other values" symbol="3" render="true"/>
    </categories>
  </renderer-v2>
</qgis>
