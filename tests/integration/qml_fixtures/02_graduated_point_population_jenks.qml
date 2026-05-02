<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.5-Prizren" styleCategories="Symbology">
  <renderer-v2 type="graduatedSymbol" attr="population" graduatedMethod="GraduatedColor" symbollevels="0">
    <symbols>
      <symbol name="0" type="marker" alpha="1">
        <layer class="SimpleMarker" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="255,255,178,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.0"/>
            <Option name="size" type="QString" value="3"/>
            <Option name="name" type="QString" value="circle"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="1" type="marker" alpha="1">
        <layer class="SimpleMarker" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="254,204,92,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.0"/>
            <Option name="size" type="QString" value="4"/>
            <Option name="name" type="QString" value="circle"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="2" type="marker" alpha="1">
        <layer class="SimpleMarker" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="253,141,60,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.0"/>
            <Option name="size" type="QString" value="5"/>
            <Option name="name" type="QString" value="circle"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="3" type="marker" alpha="1">
        <layer class="SimpleMarker" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="240,59,32,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.0"/>
            <Option name="size" type="QString" value="6"/>
            <Option name="name" type="QString" value="circle"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="4" type="marker" alpha="1">
        <layer class="SimpleMarker" enabled="1">
          <Option type="Map">
            <Option name="color" type="QString" value="189,0,38,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_width" type="QString" value="0.0"/>
            <Option name="size" type="QString" value="7"/>
            <Option name="name" type="QString" value="circle"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
    <ranges>
      <range lower="0.0" upper="1000.0" label="0 – 1000" symbol="0" render="true"/>
      <range lower="1000.0" upper="5000.0" label="1000 – 5000" symbol="1" render="true"/>
      <range lower="5000.0" upper="20000.0" label="5000 – 20000" symbol="2" render="true"/>
      <range lower="20000.0" upper="100000.0" label="20000 – 100000" symbol="3" render="true"/>
      <range lower="100000.0" upper="1000000.0" label="100000 – 1000000" symbol="4" render="true"/>
    </ranges>
    <mode name="jenks"/>
  </renderer-v2>
</qgis>
