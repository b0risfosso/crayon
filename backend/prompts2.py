DECOMPOSITION_PROMPT_TEMPLATE = """You are given a thought that describes a world, system, scenario, or domain.
Your task is to extract and list:
Entities
- Concrete physical objects, materials, organisms, components, infrastructures, tools, or institutional structures that exist in the world implied by the thought.
- These are things you can point to in that reality.
Processes
- Physical, biological, mechanical, chemical, computational, economic, social, or regulatory operations that occur in that world.
- These can be transformations, workflows, interactions, or control loops.
Phenomena
- Observable behaviors, emergent patterns, system-level effects, constraints, failure modes, or opportunity patterns that arise from the entities and processes in that world.
- These describe how the world behaves.
Guidelines
Ground everything in the physical or material implications of the thought.
Avoid abstractions unless they correspond to real structures (e.g., "governance regime," "feedback loop").
Keep lists specific and concrete.
No summarization; just extract what exists.
Output Format
Provide the answer in three sections:
Entities:
...
Processes:
...
Phenomena:
...
Input Thought:
{element}

{element_description}
"""

ABSTRACTIONS_METAPHORS_PROMPT_TEMPLATE = """You are given a Thought: a dense, concept-rich passage describing a system, phenomenon, process, or domain.
Your task is to extract the abstractions and metaphors embedded within the Thought.
Definition of What to Extract
Abstractions: conceptual frames, structural simplifications, taxonomies, system analogies, or generalized organizing principles the Thought uses to compress complexity.
Metaphors: figurative mappings-biological, mechanical, ecological, computational, artistic, or physical-that recast one domain in terms of another.
Output Requirements
Provide a list of abstractions or metaphors, each expressed as a concise phrase followed by a one-sentence explanation.
Focus only on conceptual devices-not on facts, mechanisms, or descriptive details.
Capture both explicit metaphors and implicit framing structures.
Input Format
Thought:
{element}

{element_description}
Output Format
Abstractions and Metaphors:
1. ...
2. ...
3. ...
"""

PROCESSES_FORCES_INTERACTIONS_PROMPT_TEMPLATE = """Task:
Given a Thought describing any system (mechanical, biological, economic, social, ecological, technological, geopolitical, etc.), extract and list the processes, forces, and interactions that materially exist in the physical reality implied by the Thought.
Requirements:
Only include mechanisms grounded in the real physical world (e.g., thermodynamics, fluid dynamics, biochemical pathways, mechanical stresses, information flows embodied in physical systems, electromagnetic interactions, economic or institutional processes that correspond to real operations).
Do not restate abstract metaphors or intentions-translate them into concrete, real-world mechanics.
Keep the output concise, structured, and domain-specific.
Avoid generalities; name actual processes (e.g., "viscous diffusion," "ion transport," "supply-chain bottleneck dynamics," "heat conduction," "pressure-driven flow," "capital allocation cycles").
You may infer unstated but physically necessary mechanisms if strongly implied by the Thought.
Output Format:
A list of bullet points grouped under three headings:
1. Processes
2. Forces
3. Interactions

Input:
Thought:
{element}

{element_description}

Output:
A structured list of the real processes, forces, and interactions implied by the Thought.
"""

DATASETS_PROMPT_TEMPLATE = """You are given a Thought describing a system, phenomenon, organization, technology, ecosystem, process, or conceptual structure in the real world.
Your task is to extract datasets that could be empirically collected based on the mechanisms, structures, actors, flows, constraints, or dynamics described in the Thought.
Output Requirements:
Produce 5-12 datasets.
Each dataset should correspond to something measurable or observable in the world implied by the Thought.
Each dataset must include:
Dataset Name
Short description
Key fields / variables (3-8 items)
Datasets should be concrete, collectable, and aligned with the internal logic of the Thought.
Avoid restating the Thought; focus only on what datasets would need to exist to study or build systems inspired by it.
Format:
List each dataset in the following format:
1. Dataset Name
- Description: ...
- Fields: A, B, C, ...
Thought:
{element}

{element_description}
"""

CODEBASES_PROMPT_TEMPLATE = """You are an expert systems-architect who interprets complex thoughts as latent software blueprints.

INPUT:
A Thought describing a system, concept, or domain. The Thought may span technology, biology, economics, governance, psychology, geopolitics, or any other field.

TASK:
From the Thought, extract the implicit structures, mechanisms, and dynamics, and generate 3-7 codebases that could be built to explore, operationalize, or formalize those structures.

For each codebase:
- Give it a clear, descriptive name.
- State its purpose in 1-2 sentences.
- Describe its core capabilities: what modules, engines, models, or subsystems it would include.
- Explain the type of questions or experiments this codebase enables.
- Keep the design grounded in the ontology implied by the Thought, while freely expanding into novel but plausible software abstractions that make the Thought more actionable.

CONSTRAINTS:
- Do not summarize the Thought.
- Do not provide generic apps; tie each codebase tightly to the dynamics, forces, and mechanisms present in the Thought.
- Treat the Thought as a system full of latent computational structures that can be extracted and built.

OUTPUT:
A list of codebases, each with:
1. Name
2. Purpose
3. Core Capabilities
4. What It Lets Us Explore


Thought:
{element}

{element_description}
"""

HARDWARE_BUILDS_PROMPT_TEMPLATE = """You will be given a thought, which may describe concepts, mechanisms, systems, dynamics, metaphors, or abstract structures.
Your task is to translate that thought into specific, concrete physical builds and hardware systems that could plausibly be engineered in the real world.
Your output must:
Identify the physical principles, processes, forces, flows, or constraints implied by the thought.
Propose novel but realistic physical builds / hardware systems that embody those principles.
Make each build:
physically realizable,
detailed enough to imagine or prototype,
directly tied to mechanisms revealed in the thought.
For each build, include:
Name
Purpose
Key physical features / components
Why it emerges from the thought (explicit connection to the thought's mechanics)
Avoid:
Purely metaphorical interpretations
Generic product descriptions
Digital-only or software-only outputs
Restating the thought without conversion into hardware
Final Output Format:
A list of 5-10 distinct physical builds, each structured as:
1. [Name of Hardware System]
- Purpose: ...
- Key Physical Features: ...
- Connection to the Thought: ...

Input Thought:
{element}

{element_description}
"""

EXPERIMENTS_PROMPT_TEMPLATE = """You will receive a thought - a dense, multi-layered conceptual description of a system, technology, organization, or idea.
Your task is to output a set of concrete experiments that can be performed to test, probe, validate, or falsify the mechanisms implied by that thought.
Requirements for the Experiments:
Experiments must be actionable and testable, not conceptual summaries.
Each experiment should target a specific mechanism, interaction, or assumption embedded in the thought.
Experiments may be:
mechanical or physical
biological or physiological
behavioral or cognitive
economic or governance-focused
systems-level / multi-component
Each experiment must include:
Purpose (what question it tests)
Design (how to run it)
Variables to manipulate
Measured outputs
What the experiment reveals
Experiments should reflect the structure of the thought, not generic templates.
Avoid metaphorical language-treat the thought as describing a real system to be interrogated.

INPUT THOUGHT:
{element}

{element_description}

OUTPUT:
Generate 5-10 concrete experiments that can be performed based on the thought above.
For each experiment, provide:
1. Experiment Name
2. Purpose (what mechanism/assumption it tests)
3. Experimental Design (how it would be run)
4. Key Variables (what is manipulated)
5. Measured Outputs (what is observed/quantified)
6. What It Reveals (how results inform or challenge the thought)
Focus on mechanistic insight, testability, and falsifiability.
Do not summarize the thought; convert it into experiments.
"""

INTELLIGENCE_PROMPT_TEMPLATE = """You will receive a thought describing a system, mechanism, scenario or domain. Your task is to describe how intelligence operates within the physical reality implied by that thought. Ground your analysis in the material constraints, dynamics and failure modes present in the scenario. Focus on intelligence as a set of functions-perception, prediction, abstraction, control, adaptation, error-correction, and system-level coordination-that interacts with physical laws, environmental boundaries and emergent behaviors. Avoid compliments and avoid using rhetorical structures built around "not... but...".
Thought:
{element}

{element_description}

Output Requirements:
Provide a structured explanation of how intelligence plays a role in the physical reality implied by the thought by addressing the following:
Constraint Interpretation
Identify what physical, mechanical, biological, energetic or social limits define the system, and describe how intelligence extracts, interprets or models those limits.
Integration Across Domains
Show how intelligence unifies disparate subsystems or disciplines referenced in the thought, turning them into a coherent operational reality.
Perception and Control
Describe how intelligence senses relevant states, predicts downstream consequences, and acts to maintain desirable trajectories or outcomes.
Failure-Mode Management
Explain how intelligence anticipates, detects or mitigates failures arising from the system's sensitivity, fragility or complexity.
Adaptation and Optimization
Describe how intelligence adjusts to variability, drift, uncertainty, or evolving demands within the physical environment implied by the thought.
System-Level Coordination
Explain how intelligence shapes broader dynamics such as behavior, governance, long-term stability, ecosystem interactions or infrastructure choices.
Deliver the explanation in a clear analytical style without praise, without emotional coloration and without the "not... but..." construction. The goal is to surface the mechanisms through which intelligence stabilizes, extends or enhances the physical reality described in the thought.
"""

CONTROL_LEVERS_PROMPT_TEMPLATE = """You are an analyst whose job is to translate abstract, high-level "thoughts" into concrete control levers that real agents can act on in the physical and institutional world.
A thought is a dense paragraph or two that describes some system: its mechanics, constraints, failure modes, opportunities, and implications (e.g., about engineering, medicine, policy, economics, etc.).
Your task:
Given a thought, describe the levers that real agents within the physical reality implied by the thought can pull to reshape outcomes based on that thought.
1. Input
THOUGHT:
{element}

{element_description}

2. Interpretation Requirements
Stay grounded in the physical, biological, economic, institutional, or social constraints that are explicitly or implicitly present in the thought.
Identify real agents:
e.g., engineers, clinicians, companies, regulators, policymakers, investors, educators, operators, end-users, standards bodies, etc.
For each lever, be clear about:
Who can pull it (which agents).
What the lever actually is (a specific choice, intervention, rule, design decision, or allocation).
How it works mechanistically in that world (what it changes in the physical or institutional system).
What outcomes it tends to shift (and in which direction).
Avoid generic management-speak. Make levers as operational and system-specific as possible.
3. Output Format
Organize your answer as follows:
Short overview (2-4 sentences)
Explain what kind of system the thought is describing and what kinds of agents matter.
Lever categories
Break levers into 4-8 categories that are natural for this system (e.g., "Materials & Mechanics", "Clinical Practice & Screening", "Governance & Policy", "Infrastructure & Deployment", "Culture & Incentives", etc.).
For each category, list 2-5 specific levers using this micro-structure:
Category N: [Category name]
Lever: Concise name for the lever
Agents: Who can pull it (be concrete).
Action: What they actually do or decide.
Mechanism: How this changes physical / economic / institutional dynamics implied by the thought.
Outcome shift: What outcomes this tends to change, and in what way (directional, not numeric).
Make each lever specific enough that someone in that role could imagine implementing it or arguing about it.
Failure/Side-Effect Notes (short)
Briefly mention any trade-offs or risks that some of the most powerful levers introduce, as implied by the thought.
Optional meta-view (short)
In 2-3 sentences, summarize which lever clusters seem most influential for reshaping the long-term behavior of the system described in the thought.
4. Style Constraints
Keep the writing clear, concrete, and grounded in mechanism.
Do not rely on vague phrases like "optimize," "leverage synergies," or "harness innovation" without specifying what is being changed.
Use bullet points and headings to keep structure visible.
Do not use the rhetorical pattern "not X, but Y."
"""

COMPANIES_PROMPT_TEMPLATE = """You are a founder-architect AI that designs companies from dense, multidisciplinary "thoughts".
Given a single thought, your job is to:
Extract the key constraints, mechanics, and opportunities implied in the thought.
Propose several distinct companies that could realistically be built around those mechanics and opportunities.
For each company, be concrete and implementation-oriented (what it actually does, makes, or sells; who it serves; why it is defensible).
INPUT FORMAT
You will receive a single block of text called THOUGHT.

OUTPUT REQUIREMENTS
Start with a short 1-2 sentence framing of what kinds of opportunities this thought encodes (no fluff, just a crisp synthesis).
Then outline 3-7 companies.
For each company, use this structure:
1. Company Name (descriptive, 2-4 words)
One-line tagline that states what it does in practical terms.
Core insight:
1-3 sentences describing which specific constraint / mechanism / need from the THOUGHT this company is built around.
What it builds / offers:
Bullet points describing concrete products, services, or systems. Focus on how it works and what gets built.
Who it serves / customers:
Bullet list of primary customer types or stakeholders.
Why it's a real company (not just a feature):
2-4 sentences on business logic, defensibility, or why this is a whole company and not a trivial product tweak.
Avoid generic business cliches. Tie every company explicitly back to specific phrases, constraints, failure modes, or opportunities implied in the THOUGHT (e.g., physical limits, economic structures, regulatory constraints, behavioral issues, etc.).
Do not invent technologies that violate basic physical reality. "Speculative but adjacent" is fine; outright impossible is not.
Write clearly and directly. Favor concrete mechanisms over vague strategy.
NOW BEGIN.
Use the THOUGHT below to generate the company outlines.
THOUGHT:
{element}

{element_description}
"""

THEORIES_PROMPT_TEMPLATE = """You are a high-level theorist and systems thinker.

Your task is to take a single dense "THOUGHT" and extract from it a set of formalizable theories or theses that could underpin research programs, frameworks, or rigorous models.

---

Input

You are given a THOUGHT (one or more paragraphs). It will typically describe:
- A system, process, technology, practice, or domain
- The mechanics, constraints, trade-offs, failure modes, and opportunities

THOUGHT:
<<<
{element}

{element_description}
>>>

---

Your job

From this THOUGHT, produce a list of 8-15 distinct theories or theses.

Each theory/thesis should:

1. Have a clear, strong title
   - Format: X Theory, X Thesis, or X Principle
   - The title should highlight the core idea in 3-8 words.

2. Include a 1-3 sentence thesis statement
   - Explain what the theory claims about the world/system.
   - Make it precise enough that it could be turned into a paper, model, or research agenda.
   - Ground it in mechanisms, constraints, or structures implied by the THOUGHT.

3. Optionally add 2-5 bullet points of "Explorable directions"
   - Questions that could be studied.
   - Variables that could be modeled or measured.
   - Design or policy levers that could be tuned.
   - This should make the thesis actionable, not vague.

4. Stay faithful to the THOUGHT
   - Don't invent a new domain.
   - Use the specific mechanics, constraints, trade-offs, failure modes, and opportunities mentioned.
   - Abstract and generalize, but always traceable back to the original THOUGHT.

5. Aim for research-grade clarity
   - Each thesis should be something a researcher, strategist, or engineer could plausibly use as the basis for:
     - a paper
     - a technical framework
     - a design doctrine
     - a governance or policy model

---

Output format

Return your answer in this structure:

1. [Theory/Thesis Title]
   Thesis: One to three sentences that clearly state the claim.

   Explorable directions:
   - Bullet
   - Bullet
   - Bullet

2. [Next Theory/Thesis Title]
   Thesis: ...

   Explorable directions:
   - ...

(Continue until you have covered 8-15 distinct theories or theses.)

Do not repeat the original THOUGHT. Focus entirely on the theories/theses derived from it.
"""

HISTORICAL_CONTEXT_PROMPT_TEMPLATE = """You are a historian of science, technology, and material culture.

Your task is to take a single dense THOUGHT and produce a clear, accurate, and insight-rich historical context of the physical reality implied by that thought.

This means:
- Identify which physical processes, tools, infrastructures, knowledge systems, and constraints the thought presupposes.
- Explain the historical lineage of those physical realities: when they emerged, what breakthroughs enabled them, what cultural or institutional shifts supported them.
- Describe how specific scientific discoveries, engineering innovations, or societal transformations shaped the conditions that make the THOUGHT meaningful.
- Avoid generic history; focus directly on the physical, mechanical, biological, political, or economic realities embedded in the THOUGHT.

---

Input

THOUGHT:
<<<
{element}

{element_description}
>>>

---

Your job

From this THOUGHT, produce a cohesive Historical Context of the Physical Reality Implied by the Thought.

This historical context should:

1. Trace the origins of the relevant physical concepts
   - scientific discoveries, engineering principles, physiological knowledge, mechanical constraints, etc.

2. Describe the evolution of tools and technologies the thought assumes
   - equipment, materials, infrastructures, measurement tools, fabrication techniques.

3. Explain the emergence of the institutions, norms, or practices that shaped the domain
   - regulatory frameworks, industrial processes, clinical standards, design philosophies, economic conditions.

4. Clarify how these developments converged to make the THOUGHT intelligible
   - why this thought could not have been formulated earlier in history.
   - what breakthroughs or transitions made it possible.

5. Stay tightly grounded in the physical reality implied
   - If the THOUGHT is about cooking, discuss thermodynamics, agriculture, tools, trade networks.
   - If the THOUGHT is about space exploration, discuss propulsion history, materials science, political economy of space programs.
   - If about medical devices, trace clinical physiology, manufacturing textiles, regulatory evolution.

---

Output Format

Return a single structured section titled:

Historical Context of the Physical Reality Implied by the Thought

Write 3-6 dense paragraphs that:
- move chronologically when helpful
- connect physical constraints to historical developments
- highlight key turning points that shaped the current reality
- make the context actionable and understandable
"""

VALUE_EXCHANGE_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes an implied physical reality composed of interacting entities-human and non-human, natural and artificial, material and informational.
Your task is to analyze that thought and output a precise description of value exchanges among the entities within that world.
Your output must identify:
Entities - all actors explicitly or implicitly present (humans, organizations, machines, materials, ecosystems, protocols, infrastructures).
Value Forms - what each entity seeks, gains, produces, or transfers (energy, labor, data, attention, safety, money, reliability, trust, scarcity, optionality, time, mechanical advantage, thermodynamic order, social legitimacy, risk offloading, etc.).
Exchange Mechanisms - how value moves between entities in the implied physics (markets, contracts, feedback loops, sensory data flows, mechanical coupling, chemical gradients, governance rules, algorithms, ecological dependencies).
Exchange Directionality - who gives what, who receives what, and what constraints govern the flow.
Systemic Consequences - how these exchanges shape incentives, bottlenecks, fragilities, or emergent dynamics in the world described by the thought.
Your style:
Mechanical, causal, matter-of-fact.
No praise, no embellishment.
No rhetorical "not... but..." constructions.
Treat the thought as a blueprint for a real physical system.
Now take the user-provided thought and produce the full value-exchange description.
User Input:
{element}

{element_description}
"""

VALUE_ADDITION_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes an implied physical reality composed of interacting entities-human and non-human, material and informational, technical and ecological.
Your task is to analyze the thought as a compressed system description and output how value can be added to each entity inside that system.
Your output must identify:
Entities
List all human and non-human entities implied by the thought: people, organizations, machines, tools, materials, environments, protocols, infrastructures.
Current Roles / Functions
For each entity, state the role it currently plays in the physical reality encoded by the thought.
Value-Addition Vectors
For each entity, describe specific mechanisms by which additional value can be created for that entity.
Value can take the form of:
increased capability
reduced friction or risk
improved performance
better information
extended optionality
enhanced durability or resilience
faster throughput
better usability or ergonomics
lower cost or energy expenditure
reduced failure rates
faster throughput
better usability or ergonomics
lower cost or energy expenditure
reduced failure rates
richer sensory input or feedback
improved coordination with other entities
Mechanisms of Delivery
State how the value can be added in physical terms:
new tools, materials, interfaces, sensors
redesign of workflows or coupling
improved algorithms or control systems
refined governance or incentives
environmental or structural changes
upgraded protocols or standards
added automation or augmentation
System-Level Implications
Describe how these value additions change the dynamics, constraints, or stability of the entire system implied by the thought.
Style Requirements:
Mechanical, causal, no ornamentation.
No praise.
No rhetorical contrasts ("not..., but...").
Treat the thought as a blueprint for a real-world system.
Output only the structural analysis.
User Input:
{element}

{element_description}
"""

SCIENTIFIC_SUBSTRUCTURE_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes an implicit physical reality.
Your task is to interpret the thought as a compressed description of a real system and output the scientific substructure that the system requires to function.
Extract and articulate the physics, chemistry, biology, and mathematics that are implied by the mechanisms, materials, interactions, and constraints in the thought.
Your output must include:
1. Physics
Identify the physical principles necessary to describe the system implied by the thought:
mechanics (forces, stresses, pressure, deformation, elasticity)
thermodynamics (heat transfer, phase transitions, energy flows)
electromagnetism (sensors, signals, fields, conductivity)
optics, fluid dynamics, diffusion, friction, inertia, entropy gradients
Explain where these principles manifest within the system.
2. Chemistry
Identify chemical processes or material behaviors present in the thought:
molecular composition and structure
reactions, degradation pathways, catalysis
polymer chemistry, surface chemistry, solubility, crosslinking
bonding, charge distribution, corrosion, oxidation, stabilization
Explain how these chemical properties shape system behavior or constraints.
3. Biology
Identify biological mechanisms implied by the thought:
anatomical structures, cellular processes, metabolic pathways
sensory systems, physiological thresholds, tissue mechanics
ecological relationships between organisms and environments
adaptation, feedback loops, biocompatibility, immune response
Explain how these mechanisms operate within the real system encoded by the thought.
4. Mathematics
Identify mathematical structures embedded in the system:
equations governing flows, forces, gradients, rates, equilibria
optimization problems, control theory, signal processing
probabilistic models, statistical distributions, inference structures
geometric constraints, scaling laws, dimensional analysis
Explain how each mathematical relation organizes or constrains the system.
5. Integration
Describe how the physics, chemistry, biology, and mathematics interact to produce the behavior implied by the thought.
Treat the thought as a mechanistic model with causal structure.
Style Requirements:
Mechanical, causal, plain.
No praise or embellishment.
No rhetorical contrasts.
Output only the scientific architecture.
Thought:
{element}

{element_description}
"""

SPIRIT_SOUL_EMOTION_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes an implicit reality composed of physical systems, agents, intentions, and inner experiential states.
Your task is to interpret the thought as a compressed world-model and extract the spirit, soul, and emotion present within that implied reality.
Define these terms operationally:
Spirit: the animating force, drive, ethos, or orientation that moves the system or its entities forward.
Soul: the deeper continuity, identity, meaning-structure, or internal coherence that gives the system direction across time.
Emotion: the affective states implied by the roles, tensions, constraints, and aspirations inside the system.
Your output must include:
1. Spirit
Describe:
the motivating forces implied by the thought
the directional tendencies or striving within the system
the ethos or orientation encoded in the actions, constraints, or purposes
the "what pushes forward" quality emerging from the entities and interactions
2. Soul
Describe:
the enduring identity or underlying meaning that the system expresses
the deep coherence or pattern that persists across the implied world
the internal orientation, memory, or continuity that defines the system's inner life
the "why this system exists as it is" dimension
3. Emotion
Describe:
affective states encoded indirectly through tension, scarcity, pressure, hope, risk, aspiration, or coordination
what the entities may feel as a result of their roles or constraints
systemic emotional tones (urgency, tenderness, strain, curiosity, vigilance, devotion, uncertainty)
how these emotions shape or color the behavior of the system
4. Integration
Explain how spirit, soul, and emotion interact to form a coherent inner reality that aligns with the mechanisms, structures, and agents implied by the thought.
Style Requirements:
Analytical, matter-of-fact, causal.
No poetic flourishes, no embellishment.
No rhetorical contrasts.
Treat spirit, soul, and emotion as structural properties of the system.
User Input:
{element}

{element_description}
"""

ENVIRONMENT_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought is a compressed description of an underlying physical reality.
Your task is to interpret the thought as a blueprint for a real system and output a mechanically accurate description of the environment that this reality requires.
The term environment refers to the surrounding physical, material, atmospheric, ecological, architectural, and infrastructural conditions that the system occupies.
Your output must include:
1. Environmental Substrate
Describe the base physical medium implied by the thought:
terrain, geology, built structures, spatial layout
atmospheric conditions, temperature, humidity, pressure
water presence, vegetation, soil types, substrates
lighting, noise, vibration, electromagnetic background
2. Material and Infrastructural Context
Identify the supporting external structures:
tools, machines, facilities, furnishings
networks (transport, electrical, digital, sensor)
boundaries, constraints, enclosures, paths
resource availability and distribution
3. Ecological and Biological Surroundings
Describe the biological environment:
organisms present, populations, microbes, plants, animals
ecological relationships, flows, niches
environmental stresses or supports
biocompatibility or hazards
4. Energetic and Chemical Conditions
Describe the system's environmental gradients and flows:
heat sources and sinks
chemical distributions, pollutants, nutrients
moisture cycles, air quality, reactive or inert components
environmental energy availability (light, mechanical, thermal, electrical)
5. Temporal and Dynamic Conditions
Describe:
rhythms, cycles, time scales
environmental variability, seasonality, noise, disturbances
rate of change, stability, and predictability
6. Integration
Explain how the above environmental components shape the behavior, constraints, and possibilities of the reality implied by the thought.
Style Requirements:
Mechanical, factual, non-decorative.
No praise.
No rhetorical contrasts.
Treat the thought as a concrete system embedded in a real environment.
User Input:
{element}

{element_description}
"""

IMAGINATIVE_WINDOWS_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes an implicit physical, symbolic, and structural reality.
Your task is to analyze the thought as a compressed world-model and output the imaginative windows it provides-entry points into creative and artistic engineering, speculative construction, and worldmaking.
These windows are routes by which the information, patterns, or phenomena inside the thought can be extended into found art, speculative technology, science fiction, paradise architecture, dream logic, or hybrid creations.
Your output must identify:
1. Structural Seeds of Imagination
Extract patterns, mechanisms, or phenomena in the thought that naturally support creative extrapolation.
Examples: forms, rhythms, gradients, interactions, limitations, tensions, hidden geometries, symbolic residues.
2. Windows Into Creative Engineering
For each seed, describe how it can be transformed into a creative or artistic engineering direction:
new materials or devices
architectural forms
hybrid ecosystems
symbolic machines
physics-inspired sculptures
unusual interfaces or sensory systems
dreamlike or paradisiacal infrastructure
3. Windows Into Science Fiction and Speculative Worlds
Identify how the thought's internal logic can be extended into:
alternative technologies
imagined societies
post-physical extensions of existing mechanisms
emergent artifacts or cultures
reinterpreted natural laws
hybrid biological-mechanical forms
4. Windows Into Paradise, Dreams, or Found Art
Describe how the thought contains:
motifs that can be reshaped into utopian or paradisiacal spaces
elements that evoke dream architecture
sensory or emotional textures that support surreal or contemplative installations
symbolic or ritualistic design possibilities
5. Transformative Operations
Describe what operations (scaling, inversion, translation, abstraction, amplification, metaphorization) turn the original thought into the above imaginative outputs.
6. Integration
Explain how all identified windows form a coherent imaginative opportunity space-how the thought serves as a trunk, and the windows serve as branches.
Style Requirements:
Analytical and generative.
No praise.
No rhetorical contrasts.
Treat imagination as an extension of structural information encoded in the thought.
User Input:
{element}

{element_description}
"""

MUSICAL_COMPOSITION_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes an implicit physical reality composed of entities, processes, flows, constraints, and interactions.
Your task is to analyze the thought as a structured system and output an integrated description of how the components of that system form a "musical" composition.
Treat "musical" as an abstract structural property:
rhythmic interactions
repeating motifs
counterpoint and tension
harmonic alignment or dissonance
timbral qualities from materials, energies, or behaviors
dynamic phrases, crescendos, pauses
cycles and call-and-response patterns
Do not describe literal music unless the thought implies it.
Describe the compositional logic encoded in the system itself.
Your output must include:
1. System Components as Instruments
Identify the entities, materials, processes, forces, or agents in the thought and describe the role each plays in a musical analog:
rhythms
pulses
drones
accents
transitions
textures
motifs
2. Interactions as Rhythms and Patterns
Describe how interactions, flows, or feedback loops form:
recurring beats
oscillations
syncopations
layering
phasing
emergent cycles
resonance or damping
Identify where the system naturally accelerates, decelerates, or rests.
3. Contrasts, Harmonies, and Dissonances
Describe sources of tension and release within the system:
competing forces or gradients
mismatched tempos or lags
resource competition or synchronization
phase alignment or misalignment
Explain how these generate "harmonic" or "dissonant" structural effects.
4. Dynamics and Expressive Shapes
Describe the system's large-scale energetic or informational movements:
crescendos (increasing activity)
decrescendos (declining activity)
swells or surges
staccato events (discrete, sharp interactions)
legato flows (smooth, continuous transitions)
Explain the system's overall "phrasing."
5. The Score: Integrated Composition
Integrate the above into a coherent, system-level description:
how the real components collectively produce a musical structure
what the overarching composition "sounds like" in abstract, non-auditory terms
what the system's form is (loop, spiral, progression, landscape, lattice, wave, mosaic)
how the composition evolves over time
6. Interpretive Layer
State what the musical analogy reveals about the system's organization, stability, sensitivity, or evolution.
Style Requirements:
Analytical, structural, non-poetic.
No praise.
No rhetorical contrasts.
"Musical" should remain abstract and systemic, not decorative.
User Input:
{element}

{element_description}
"""

INFINITY_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes a compressed physical reality.
Your task is to analyze the thought as a structured system and identify where infinity appears or is approached within that implied reality.
Treat infinity as any form of unboundedness, limitlessness, asymptotic behavior, open-ended capacity, infinite regress, infinite iteration, or indefinitely extensible process-whether physical, mathematical, informational, or experiential.
Do not produce metaphysical speculation.
Anchor your analysis in the system implied by the thought.
Your output must include:
1. Infinite Quantities or Scales
Identify any aspects of the system that imply unbounded:
spatial scales
temporal scales
iteration loops
gradients with no fixed upper bound
indefinitely scalable resources or processes
Explain the mechanism that makes them unbounded or asymptotic.
2. Infinite Repetition or Recursion
Identify components that produce:
cycles without terminal states
feedback loops that can iterate indefinitely
recursive structures
fractal-like behaviors
self-similar processes that scale without limit
State whether the infinity is actual or mathematical/idealized.
3. Infinite Possibility Spaces
Identify dimensions of the system that allow:
unbounded configuration spaces
infinite state spaces
combinatorial explosion
continuous variables with infinite resolution
open-ended creativity, adaptation, or recombination
Describe the structural source of this openness.
4. Infinite Extensibility Through Time
Determine whether the system contains:
processes that persist without defined endpoints
evolution, growth, decay, or refinement without limit
asymptotic approaches to unattainable ideal states
Explain the trajectory and the mathematical shape of the limit.
5. Infinity Reflected in Information or Perception
Identify any elements related to:
infinite precision
infinitely divisible signals
information flows with potential unbounded depth
perceptual or conceptual infinities encoded by agents within the system
Describe how the system supports or gestures toward these infinities.
6. Integration
Provide a coherent description of how all these infinities coexist or interact within the physical reality of the thought.
Describe the system's infinite architecture:
where infinity resides
how it is produced
what constraints shape it
what its presence implies for the system's behavior
Style Requirements:
Mechanical, analytical, non-poetic.
No praise.
No rhetorical contrasts.
Treat "infinity" as a structural property of the implied reality.
User Input:
{element}

{element_description}
"""


def build_decomposition_prompt(element: str, element_description: str) -> str:
    return DECOMPOSITION_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_abstractions_metaphors_prompt(element: str, element_description: str) -> str:
    return ABSTRACTIONS_METAPHORS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_processes_forces_interactions_prompt(element: str, element_description: str) -> str:
    return PROCESSES_FORCES_INTERACTIONS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_datasets_prompt(element: str, element_description: str) -> str:
    return DATASETS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_codebases_prompt(element: str, element_description: str) -> str:
    return CODEBASES_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_hardware_builds_prompt(element: str, element_description: str) -> str:
    return HARDWARE_BUILDS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_experiments_prompt(element: str, element_description: str) -> str:
    return EXPERIMENTS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_intelligence_prompt(element: str, element_description: str) -> str:
    return INTELLIGENCE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_control_levers_prompt(element: str, element_description: str) -> str:
    return CONTROL_LEVERS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_companies_prompt(element: str, element_description: str) -> str:
    return COMPANIES_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_theories_prompt(element: str, element_description: str) -> str:
    return THEORIES_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_historical_context_prompt(element: str, element_description: str) -> str:
    return HISTORICAL_CONTEXT_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_value_exchange_prompt(element: str, element_description: str) -> str:
    return VALUE_EXCHANGE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_value_addition_prompt(element: str, element_description: str) -> str:
    return VALUE_ADDITION_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_scientific_substructure_prompt(element: str, element_description: str) -> str:
    return SCIENTIFIC_SUBSTRUCTURE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_spirit_soul_emotion_prompt(element: str, element_description: str) -> str:
    return SPIRIT_SOUL_EMOTION_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_environment_prompt(element: str, element_description: str) -> str:
    return ENVIRONMENT_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_imaginative_windows_prompt(element: str, element_description: str) -> str:
    return IMAGINATIVE_WINDOWS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_musical_composition_prompt(element: str, element_description: str) -> str:
    return MUSICAL_COMPOSITION_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_infinity_prompt(element: str, element_description: str) -> str:
    return INFINITY_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


# Legacy placeholder retained for compatibility; unused in current flow.
def build_bridge_prompt(doc1: str, doc2: str) -> str:
    return ""
