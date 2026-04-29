<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.5-Prizren" styleCategories="Symbology" maxScale="0" minScale="100000000">
  <renderer-v2 type="RuleRenderer" symbollevels="0">
    <rules key="root">
      <rule key="major-roads" filter="&quot;highway&quot; IN ('motorway','trunk','primary')" label="Major roads" symbol="0"/>
      <rule key="secondary-roads" filter="&quot;highway&quot; IN ('secondary','tertiary')" scalemindenom="0" scalemaxdenom="50000" label="Secondary roads" symbol="1"/>
      <rule key="minor-roads" filter="&quot;highway&quot; IN ('residential','service','unclassified')" scalemindenom="0" scalemaxdenom="10000" label="Minor roads" symbol="2"/>
    </rules>
    <symbols>
      <symbol name="0" type="line" alpha="1">
        <layer class="SimpleLine" enabled="1">
          <Option type="Map">
            <Option name="line_color" type="QString" value="234,89,89,255"/>
            <Option name="line_width" type="QString" value="2.5"/>
            <Option name="capstyle" type="QString" value="round"/>
            <Option name="joinstyle" type="QString" value="round"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="1" type="line" alpha="1">
        <layer class="SimpleLine" enabled="1">
          <Option type="Map">
            <Option name="line_color" type="QString" value="246,166,42,255"/>
            <Option name="line_width" type="QString" value="1.5"/>
            <Option name="capstyle" type="QString" value="round"/>
          </Option>
        </layer>
      </symbol>
      <symbol name="2" type="line" alpha="1">
        <layer class="SimpleLine" enabled="1">
          <Option type="Map">
            <Option name="line_color" type="QString" value="180,180,180,255"/>
            <Option name="line_width" type="QString" value="0.7"/>
            <Option name="capstyle" type="QString" value="round"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
</qgis>
