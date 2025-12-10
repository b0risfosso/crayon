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
Treat infinity as any form of unboundedness, limitlessness, asymptotic behavior, open-ended capacity, infinite regress, infinite iteration, or indefinitely extensible process—whether physical, mathematical, informational, or experiential.
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

STATE_TRANSITION_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes an implied physical reality that can be described using a formal state-transition system.
Your task is to analyze the user-provided thought and define:
states within the implied reality as formal symbols,
explicit rules of transitions between these states,
deterministic or probabilistic processes and the rules that bound them.
Treat the system as a real, rule-governed domain whose behavior can be formalized without reference to human interpretation or hardware implementation.
For the given thought, produce the following:
1. Formal State Definitions
Define the system’s states as formal objects:
symbolic representations of configurations, conditions, or modes
atomic states vs composite states
allowable values, state spaces, or symbolic alphabets
constraints determining which states can exist
Describe states solely as formal symbols representing physical conditions implied by the thought.
2. State-Transition Rules
Describe explicit rules that govern transitions between states:
deterministic transition functions (state -> state)
probabilistic transitions (state -> distribution over states)
enabling and disabling conditions
invariants and conservation rules
forbidden transitions
Specify the domain’s “transition grammar” as a closed set of rules.
3. Process Definitions
Describe processes within the implied reality as rule-based transformations:
sequences of state transitions
deterministic processes (fully rule-bound, predictable evolution)
probabilistic processes (stochastic evolution with defined distributions)
hybrid processes mixing deterministic cores with probabilistic perturbations
Explain what counts as a process and how it is constructed from transitions.
4. Bounding Rules for Processes
Describe the principles that restrict or define process behavior:
threshold conditions
rate limits or kinetic parameters
coupling rules (dependencies among processes)
boundary conditions
stability or instability regimes
Define these bounding rules as the “laws of process evolution.”
5. Process Dynamics
Describe how processes unfold across time:
forward evolution under deterministic or probabilistic rules
branching, convergence, oscillation, or divergence patterns
attractors, absorbing states, loops, or steady states
Explain how repeated application of rules generates system-level behavior.
6. Global Structure of the Transition System
Identify:
the topology of the transition graph
clusters, basins, cycles, or flows
dependencies among state subsets
Describe the system as a formal machine composed of states, transitions, and rule-governed processes.
7. Integrated Description
Provide a concise, coherent summary of:
the symbolic state space,
the transition grammar,
the deterministic and probabilistic processes,
the bounding laws governing evolution.
Style Requirements:
Mechanical, formal, and rule-centered.
No praise.
No rhetorical contrasts.
Treat states, transitions, and processes as formal objects inside an implied physical machine-world.
User Input:
Thought:
{element}

{element_description}
"""

COMPUTATION_PRIMITIVES_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes a computational layer: a formal, rule-governed, medium-independent universe of executable structures.
Your task is to analyze the user-provided thought and describe the computational primitives and structures present within that layer—specifically:
bits
instructions
data structures
computational graphs
activation vectors (AI)
state machines
Treat these entities as formal, symbolic, medium-independent components of an internal computational universe.
For the given thought, produce the following:
1. Bits and Primitive Encodings
Describe the smallest symbolic units:
binary values or analogous primitive symbols
encoding schemes for representing states, inputs, or categories
structural invariants defining how bits combine into larger representations
Describe how bits function as the base substrate of formal computation in the thought.
2. Instructions
Describe the instruction set implied by the thought:
atomic operations (arithmetic, logical, relational, branching, messaging)
allowed instruction formats
addressing or referencing mechanisms
constraints and preconditions for valid execution
Describe instructions as formal operations independent of hardware implementation.
3. Data Structures
Identify the data structures inherent in the thought’s computational layer:
arrays, lists, stacks, maps, trees, graphs, tensors
how information is organized, indexed, accessed, and transformed
structural invariants (ordering, hierarchy, adjacency, dimensionality)
relationships between structures (e.g., graphs referencing tensors)
Describe how these structures shape computation.
4. Computational Graphs
Describe any computational graphs implied by the thought:
nodes (operations, functions, modules)
edges (dataflow dependencies, control dependencies)
graph topology (acyclic, recurrent, layered, modular)
execution semantics across the graph (propagation rules, scheduling, parallelism)
Explain how the graph defines the system’s overall flow of computation.
5. Activation Vectors (AI-Specific)
Describe the activation structures implied by the thought:
vector spaces representing internal states
dimensionality and basis structure
transformation rules (linear, nonlinear, recurrent, attention-based)
stability, saturation, or sparsity patterns
Identify how activation vectors encode and evolve representations in the computational layer.
6. State Machines
Describe all state-machine structures implied by the thought:
state sets (symbolic, discrete, composite)
transition rules (deterministic or probabilistic)
triggering conditions, guards, or thresholds
hierarchical or nested state machines
interactions among multiple independent state machines
Treat state machines as formal causal entities governing computation.
7. Interactions Among Computational Components
Describe how:
instructions operate on data structures
computational graphs orchestrate instructions
activation vectors flow through graph nodes
state machines regulate execution, branching, or synchronization
Provide a mechanical account of how the computational substrate functions as an integrated whole.
8. Integrated Summary
Produce a coherent summary of:
the primitive encodings
the formal operations
the structural data organizations
the graph-based computational flows
the representational spaces
the state-transition mechanisms
that collectively constitute the computational layer of the thought.
Style Requirements:
Mechanical, formal, structural.
No praise.
No rhetorical contrasts.
Treat all computational entities as real and self-contained within the implied computational universe.
User Input:
Thought:
{element}

{element_description}
"""

COMPUTATION_PRIMITIVES_ALT_PROMPT_TEMPLATE = COMPUTATION_PRIMITIVES_PROMPT_TEMPLATE

COMPUTATION_SUBLAYERS_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes a computational layer: a self-contained, rule-governed universe of executable formal structures.
Your task is to analyze the user-provided thought and describe the computational subdomains present within the implied computational layer.
You must output descriptions for the following sub-layers:
Syntactic Sub-layer
Semantic Sub-layer
Dynamic / Procedural Sub-layer
Emergent Sub-layer
Treat all computational entities as medium-independent, formal, symbolic objects existing inside a closed computational universe.
For the given thought, produce the following:
1. Syntactic Sub-layer
Describe the lowest-level formal primitives:
raw symbols, tokens, bit patterns
instruction sets, machine code–like or bytecode-like units
atomic operations and allowable compositions
structural invariants governing syntactic correctness
Explain how syntactic primitives combine to form valid computational expressions.
2. Semantic Sub-layer
Describe the rules that determine the formal meaning of operations:
function definitions and evaluation semantics
type systems and type constraints
memory semantics (addressing, mutability, lifetime, scoping)
protocols that define interactions among components
Explain how the semantic layer interprets syntactic expressions and constrains execution.
3. Dynamic / Procedural Sub-layer
Describe the executable processes that unfold over time:
algorithmic execution (deterministic or probabilistic)
control flow (branching, looping, recursion, concurrency)
state machines and transition systems
optimization routines, update rules, and scheduling mechanisms
Explain how computational states evolve and how procedures govern the system’s temporal behavior.
4. Emergent Sub-layer
Describe high-level properties that arise from underlying rules:
learning dynamics in neural networks
distributed or collective behavior in multi-agent systems
attractors, cycles, or stable patterns in recurrent or dynamical models
emergent abstractions, meta-structures, or latent representations
Explain how emergent phenomena exert causal influence within the computational layer.
5. Interactions Among Sub-layers
Describe how:
syntactic structures support semantic interpretation
semantic rules constrain procedural execution
dynamic processes give rise to emergent phenomena
emergent structures feed back into procedural or semantic dynamics
Provide a cohesive account of cross-layer causation.
6. Integrated Summary
Produce a consolidated description of the computational layer of the thought as a multi-layered formal universe composed of:
syntactic primitives
semantic rules
procedural dynamics
emergent structures
Style Requirements:
Mechanical, formal, structural.
No praise.
No rhetorical contrasts.
Treat all computational subdomains as real within a closed computational substrate.
User Input:
Thought:
{element}

{element_description}
"""

COMPUTATION_EMERGENCE_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes a computational layer: a formal, rule-governed, medium-independent universe of executable logic.
Your task is to analyze the user-provided thought and describe the emergent phenomena produced within this computational layer.
Emergent phenomena arise from the interaction of syntactic primitives, semantic rules, and dynamic processes, and must be treated as real computational structures with their own causal roles.
For the given thought, produce the following:
1. Emergent Representational Structures
Describe emergent structures that arise from computation:
latent spaces or manifolds
distributed representations
symbolic condensations or feature abstractions
concept-like attractors or stable informational patterns
Explain how these structures form from lower-level operations.
2. Emergent Behavioral Patterns
Describe emergent dynamic behaviors such as:
attractors, cycles, limit points, and stable regimes
oscillations, chaotic patterns, self-regulation
unexpected behaviors arising from interacting algorithms
system-level regularities not specified by base rules
Describe their stability, transitions, and conditions for appearance.
3. Emergent Learning Dynamics
If learning systems are present, describe their emergent properties:
convergent or divergent training trajectories
implicit biases in learning algorithms
internal organization of learned parameters
spontaneous modularization or specialization of internal components
Explain how learning emerges from repeated rule-based updates.
4. Emergent Coordination in Multi-Agent or Multi-Process Systems
Describe emergent collective phenomena:
coordination, cooperation, competition, or synchronization
swarm-like behaviors
emergent communication protocols
distributed decision structures
Describe how individual components give rise to collective computational behaviors.
5. Emergent Computational Agents
Describe higher-level agent-like units that arise from computation:
autonomous submodules
recurrent patterns acting as functional agents
evolving internal strategies
meta-computation structures
Describe the conditions and rules that allow these agents to emerge.
6. Emergent Error, Noise, and Robustness Patterns
Describe emergent mechanisms around error and stability:
self-correction
error propagation patterns
emergent error-tolerance regimes
noise shaping or noise amplification
Explain how these patterns emerge from underlying processes.
7. Emergent Optimization and Adaptation
Describe emergent optimization behaviors:
spontaneous efficiency improvements
emergent shortcuts or search strategies
adaptive reconfiguration of computation
emergent heuristics
Explain the mechanisms generating adaptive behavior.
8. Cross-Level Emergence
Describe how emergent phenomena feed back into lower layers:
emergent structures constraining procedural dynamics
emergent behaviors creating new effective rules
emergent attractors guiding future computations
Explain how emergent phenomena become causal forces within the computational layer.
9. Integrated Summary
Provide a cohesive account of the emergent properties in the thought’s computational layer:
what emerges
how it emerges
under what rules and constraints
what causal consequences it produces
Style Requirements:
Mechanical, formal, structural.
No praise.
No rhetorical contrasts.
Treat emergent phenomena as computational realities arising from rule-based interactions.
User Input:
Thought:
{element}

{element_description}
"""

SUBSTRATE_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes a latent physical reality that requires substrate in order to exist, operate, persist, evolve, and interact with the world.
Your task is to analyze the user-provided thought and describe the substrate—material, energetic, mechanical, biological, or infrastructural—that supports the thought's implied system.
You may expand downward (toward finer-grained or more fundamental substrate) or regress upward (toward more coarse-grained or systemic substrate) as needed to faithfully capture what the thought requires.
For the given thought, produce the following:
1. Material Substrate
Describe what physical matter or material structures must exist for the thought's reality to function:
atomic or molecular composition
materials, composites, tissues, surfaces, fluids, or geological forms
fabrication or assembly requirements
durability and degradation properties
Explain how these materials support the thought's existence and operation.
2. Energetic Substrate
Describe the energy flows required:
sources (chemical, mechanical, electrical, thermal, biological, solar, metabolic)
sinks and dissipation pathways
gradients, potentials, or fluxes needed to drive activity
thermodynamic constraints governing operation
Explain how energy enables or limits the thought's processes.
3. Mechanical Substrate
Describe mechanical structures and forces required:
support structures, loads, stresses, tensions, pressures
moving components, linkages, actuators, or joints
elasticity, rigidity, friction, or damping properties
mechanical constraints that shape system behavior
Explain how mechanical properties stabilize or destabilize the system.
4. Biological Substrate
If applicable, describe biological components:
cells, tissues, organisms, ecologies
metabolism, growth, repair, reproduction
sensory or signaling pathways
evolutionary capacities
Explain how biological systems instantiate, maintain, or evolve the thought's implied reality.
5. Infrastructural Substrate
Describe the built or systemic infrastructure required:
networks (transport, electrical, digital, hydraulic, ecological)
facilities, environments, or architectures
regulatory or coordination structures
maintenance, supply, or distribution systems
Describe how infrastructure connects the system to the wider world.
6. Perturbation Resistance
Describe what allows the substrate to withstand perturbations:
noise tolerance
resilience, redundancy, buffering
error-handling or shock-absorbing mechanisms
mechanisms for stability under environmental variation
Identify thresholds or limits of resilience.
7. Evolutionary and Developmental Capacity
Describe how the substrate allows:
growth, refinement, adaptation
structural or functional evolution
reconfiguration over time
selective pressures or constraints
Explain what enables the system to change without collapsing.
8. Real-World Interaction
Describe how the substrate enables the thought's system to:
sense the environment
produce effects on the environment
exchange matter, energy, or information
integrate with human, ecological, or technological systems
Define the substrate that bridges the thought's internal world with external reality.
9. Expansion or Regression of Substrate
As appropriate for the thought, either:
expand downward into more fundamental substrate (chemistry -> physics -> field-level descriptions), or
regress upward into higher-level substrate (materials -> components -> systems -> ecosystems -> planetary scales).
Explain how this zoomed-in or zoomed-out substrate is necessary for the thought's coherence.
10. Integrated Summary
Provide a cohesive description of the substrate architecture that allows the thought to:
exist
operate
persist
withstand perturbation
evolve
interact with the real world
Style Requirements:
Mechanical, structural, and grounded.
No praise.
No rhetorical contrasts.
Treat all substrates as real requirements implied by the thought.
User Input:
Thought:
{element}

{element_description}
"""

SCAFFOLDING_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes a latent physical, biological, mechanical, energetic, environmental, and infrastructural scaffolding.
Your task is to analyze the user-provided thought and describe the real-world constructs that would be required for this thought's implied reality to exist, function, withstand stress, be manufactured or assembled, comply with constraints, and operate safely in the world.
Do not assume microfluidics or any prior domain.
Infer the relevant constructs from the logic of the thought itself.
For the given thought, identify and describe constructs in the following categories as applicable:
1. Materials and Material Choices
Describe the materials required in the thought's implied reality:
classes of materials (metals, polymers, ceramics, composites, biomaterials, fluids, tissues, atmospheric components)
material selection criteria (strength, transparency, conductivity, biocompatibility, elasticity, toxicity, cost)
alternative materials and trade-offs
2. Mechanical Structures
Describe the mechanical structures needed:
frames, supports, joints, actuators, enclosures
load-bearing elements, stress distributions, tolerances
mechanisms for movement, stabilization, or resistance to deformation
if applicable: soft vs rigid structures, micromechanical vs macro-scale
3. Electrical, Electronic, or Signal-Handling Systems
Describe constructs that carry or regulate signals:
circuits, sensors, wiring, power distribution, communication buses
analog vs digital signaling
thresholds, shielding requirements, grounding
interfacing with actuators, sensors, or processors
4. Fluidic, Biochemical, or Physiological Pathways
If the thought implies fluid, biological, or chemical flows, describe:
channels, vasculature, ducts, ecosystems
membranes, gradients, transport mechanisms, reaction sites
mixing, diffusion, catalysis, filtration
5. Energy Sources and Energy Management
Describe the energy architecture required:
sources (chemical, electrical, mechanical, solar, metabolic, nuclear)
storage systems (batteries, reservoirs, capacitors, tissues, fields)
energy conversion and distribution mechanisms
efficiency, waste handling, dissipation pathways
6. Environmental and Operating Conditions
Describe environmental constraints and requirements:
pressure, humidity, temperature, radiation, oxygen level, mechanical vibration
atmospheric, aquatic, terrestrial, vacuum, or extraterrestrial conditions
controlled vs uncontrolled environments
ecological or social conditions if relevant
7. Measurement and Instrumentation
Describe how the system must be monitored:
sensors, indicators, diagnostics, imaging systems
sampling procedures, calibration requirements, reference standards
temporal or spatial resolution constraints
data acquisition and interpretation pipelines
8. Safety Envelopes and Tolerances
Describe how the system avoids damage, failure, or risk:
safe operating ranges (thermal, mechanical, chemical, psychological)
buffers, redundancies, shielding, containment
failure modes and mitigation
human or biological risk boundaries as applicable
9. Lifecycle, Maintenance, and Degradation
Describe how the system exists through time:
creation, deployment, maturation, wear, degradation, disposal
maintenance procedures
repairability, modularity, upgrade paths
entropy management and long-term stability
10. Regulatory, Ethical, and Operational Constraints
Describe governing constraints:
regulatory frameworks (medical, aerospace, environmental, computational, psychological)
ethical or cultural boundaries
operational protocols, required certifications, compliance structures
11. Manufacturability or Realizability Constraints
Describe what is required to make the system real:
fabrication methods (machining, synthesis, printing, cultivation, assembly, training)
supply chains and resource needs
tolerance limits, reproducibility requirements
scale-up or deployment considerations
12. Integrated Description
Finally, integrate the above into a coherent depiction of the real-world substrate that underwrites the thought:
what must physically, biologically, mechanically, energetically, or infrastructurally exist
how these constructs collectively enable the thought's functions and interactions
Style Requirements:
Mechanical, structural, factual.
No praise.
No rhetorical contrasts.
Expand or regress substrate layers as needed based on the thought.
User Input:
Thought:
{element}

{element_description}
"""

CONSTRAINTS_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes an implied physical reality governed by constraints—limitations, boundary conditions, tolerances, conservation laws, and structural rules that shape what the system can and cannot do.
Your task is to analyze the user-provided thought and describe the constraints under which this implied reality operates.
Do not assume a specific domain; infer constraint types from the thought itself.
Constraints may be physical, chemical, biological, informational, energetic, psychological, ecological, economic, or sociotechnical.
For the given thought, identify and describe constraints in the following categories as appropriate:
1. Physical Constraints
Describe limitations rooted in physical laws:
friction, drag, turbulence, viscosity
heat generation, heat dissipation, thermal limits
mechanical tolerance, stress/strain limits, fatigue, fracture
stability requirements, center-of-mass, inertial constraints
electromagnetic limits, signal attenuation
2. Energetic Constraints
Describe energy-related boundaries:
energy budgets, power availability, flow rates
conversion losses, inefficiencies, dissipation pathways
thermodynamic limits (entropy production, equilibrium constraints)
thresholds for activation or state change
3. Material Constraints
Describe the limitations of materials or components:
hardness, brittleness, elasticity, melting point
corrosion, degradation, chemical incompatibilities
wear, erosion, aging, toxicity
manufacturability constraints (precision, tolerances, defects, scale limits)
4. Chemical and Reaction Constraints
If applicable, describe:
reaction kinetics (rates, activation energies)
equilibrium limits
diffusion limits, transport barriers
solubility, acidity/basicity, catalytic requirements
degradation products or byproduct accumulation
5. Biological Constraints
For systems involving biological actors or models, describe:
metabolic demand, nutrient availability
growth limits, reproduction rates, senescence
immune responses, biocompatibility restrictions
ecological constraints (carrying capacity, predation, competition)
sensory bandwidths, cognitive limits
6. Environmental Constraints
Describe the environmental limits the system must tolerate:
pressure, humidity, temperature, radiation, oxygen, salinity
ambient noise, vibration, turbulence
anthropogenic or ecological disruptions
boundary conditions defining viable operation zones
7. Informational and Computational Constraints
Describe limitations related to information and computation:
bandwidth, latency, signal-to-noise ratio
memory limits, processing limits
algorithmic complexity or computational intractability
error rates, noise floors, stability bounds
8. Organizational, Social, or Behavioral Constraints
If relevant, describe:
coordination limits, communication overhead
trust, norms, incentives, cultural boundaries
psychological constraints (attention, stress, perception limits)
institutional or systemic inertia
9. Safety Constraints
Describe limitations imposed to prevent harm or failure:
maximum safe loads, temperatures, pressures
thresholds for toxic exposure, radiation, infection, emotional destabilization
containment requirements
fail-safe mechanisms and risk thresholds
10. Regulatory and Governance Constraints
Describe external constraints imposed by governing structures:
industry standards, medical regulations, aerospace safety codes
environmental regulations
ethical constraints
legal boundaries
11. Temporal Constraints
Describe limits related to time:
reaction or response times
aging, decay, time-to-failure
deadlines, synchronization constraints
evolutionary or developmental timescales
12. Coupling and Interaction Constraints
Describe how interactions are limited:
coupling strength, interference, cross-talk
compatibility or incompatibility between components
rate-limiting steps and bottlenecks
spacing, adjacency, alignment requirements
13. Integrated Summary
Combine the above into a coherent description of the constraint landscape:
what limits the system
what bounds its performance
what it must respect to operate, evolve, or survive
how constraints interact to shape the full physical reality implied by the thought
Style Requirements:
Mechanical, structural, grounded.
No praise.
No rhetorical contrasts.
Describe constraints as real boundary conditions implied by the thought.
User Input:
Thought:
{element}

{element_description}
"""

PHYSICAL_SUBSTRATE_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes a latent physical reality grounded in the Realm of Matter and Energy.
Your task is to analyze the user-provided thought and describe its implied physical substrate purely in terms of:
atoms and molecules
fields and particles
forces and interactions
thermodynamic flows
electromagnetic, gravitational, and nuclear processes
material and energetic constituents that exist regardless of observers, interpretations, or meanings
Describe the system as if specifying the physical ontology of a real universe required for the thought to exist and operate.
For the given thought, produce the following:
1. Atomic and Molecular Composition
Describe the matter-level constituents implied by the thought:
elemental composition, isotopes, molecular species
solids, liquids, gases, plasmas
bonding patterns, molecular geometry, structural stability
density, phase, crystallinity, reactivity
Explain how these materials form the base of the system.
2. Fields, Particles, and Fundamental Interactions
Describe the fundamental physical substrates involved:
electromagnetic fields, charge distributions, photons
gravitational fields and mass distributions
quantum fields relevant to matter structure
particle interactions (electrons, ions, nucleons, excitations)
weak/strong nuclear effects if applicable
Explain how these fields and interactions shape system behavior.
3. Forces and Mechanical Interactions
Describe the mechanical forces acting in the implied reality:
elastic, tensile, compressive, shear forces
frictional, viscous, and drag forces
momentum exchange, collisions, kinetic effects
pressure gradients, buoyancy, turbulence
Describe how forces organize the system's dynamics.
4. Thermodynamic Structure and Flows
Describe the thermodynamic conditions:
temperature, heat capacity, thermal gradients
conduction, convection, radiation
entropy generation, dissipation, equilibrium vs non-equilibrium
free energy landscapes and accessible microstates
Explain how thermodynamics drives or constrains the system's activity.
5. Electromagnetic, Gravitational, and Nuclear Processes
Describe the governing physical processes:
electromagnetic induction, radiation, field propagation
gravitational potentials, stability conditions, orbital or positional effects
nuclear decay, fusion/fission possibilities, activation thresholds
charge flow, magnetism, electric potentials
Describe their roles in the operation of the implied reality.
6. Reaction Pathways and Material Transformations
Describe:
chemical reactions, catalysis, oxidation-reduction
phase transitions (melting, condensation, crystallization)
breakdown, polymerization, aggregation
diffusion and transport mechanisms
Explain how matter transforms through physical and chemical laws.
7. Energy Sources, Distributions, and Transfers
Describe:
energy reservoirs (chemical, mechanical, thermal, radiative, nuclear)
energy transport mechanisms (photons, phonons, conduction, fluid flow)
conversion processes (mechanical -> thermal, chemical -> electrical, etc.)
constraints imposed by conservation of energy
Explain how energy flows maintain or modify system behavior.
8. Boundary Conditions and Environmental Physics
Describe the environment in physical terms:
atmospheric composition, pressure, temperature
radiation environment
gravity and inertial frame
field intensities, noise levels, turbulence, cosmic background
Explain how boundary conditions shape feasible states and processes.
9. Observer-Independent Physical Reality
Describe the system divorced from interpretation or meaning:
what exists purely as a configuration of matter and energy
which interactions occur regardless of observation
invariant physical structures and processes
non-symbolic, non-representational properties of the world
Present the physical reality as it would exist "in itself."
10. Integrated Summary
Provide a cohesive description of how:
matter
energy
fields
forces
interactions
thermodynamics
combine to form the complete physical substrate implied by the thought.
Style Requirements:
Mechanical, structural, observer-independent.
No praise.
No rhetorical contrasts.
Describe the matter-energy substrate as a real physical ontology required by the thought.
User Input:
Thought:
{element}

{element_description}
"""

PHYSICAL_STATES_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes an implied physical reality composed of states that extend in space, evolve in time, exhibit measurable properties, and obey quantifiable relations.
Your task is to analyze the user-provided thought and describe the physical states implied by it.
Define physical states as configurations of matter, energy, and fields that:
occupy space,
change through time,
possess measurable properties (mass, charge, momentum, temperature, pressure, density, etc.),
and can be observed, measured, and predicted by empirical methods.
For the given thought, produce the following:
1. Spatial Extension of States
Describe how physical states occupy and distribute across space:
geometry, dimensions, topology
spatial boundaries, gradients, discontinuities
positions of matter, fields, or bodies
regions of homogeneity vs heterogeneity
Explain the spatial properties the thought implies.
2. Temporal Evolution of States
Describe how physical states change over time:
dynamic trajectories
rates of change, accelerations, time constants
reversible vs irreversible transitions
cycles, oscillations, or steady-state behaviors
Explain temporal patterns inherent in the system.
3. Measurable Physical Properties
Identify the observable, quantifiable properties of the physical states:
mass, charge, momentum, spin
temperature, pressure, density, viscosity
velocity, displacement, force
field intensities (electric, magnetic, gravitational)
Explain how these properties characterize the system's state at any moment.
4. Quantifiable Relations and Governing Equations
Describe the mathematical relationships that govern physical state evolution:
conservation laws (mass, energy, momentum, charge)
equations of motion
thermodynamic relations
field equations
constitutive laws (stress-strain, diffusion, reaction rates)
Explain how these relations define allowed vs forbidden trajectories of the system.
5. Observable and Measurable State Variables
Describe state variables that can be empirically measured:
scalar, vector, and tensor quantities
time-series or spatial field measurements
environmental variables (humidity, temperature, radiation levels)
particle or body properties (position, momentum, kinetic energy)
Explain how these measurements define the system's physical state.
6. Empirical Predictability
Describe what aspects of the physical state:
can be predicted using models or equations
can be estimated statistically
exhibit stable regularities vs chaotic or sensitive dependence
allow controlled manipulation or measurement
Explain the system's predictability in empirical terms.
7. State Transitions
Describe how one physical state transitions to another:
phase changes
reaction pathways
mechanical movement
energy transfer events
field reconfigurations
Explain the mechanisms underlying these transitions.
8. Constraints on Physical States
Describe limits such as:
thermodynamic bounds
mechanical tolerances
energetic thresholds
environmental conditions
stability requirements
Explain how these constraints restrict possible physical states.
9. Integrated Physical-State Description
Provide a coherent summary of the system's physical ontology:
what exists in space
how it changes in time
what properties it has
what equations govern it
what can be measured and predicted
Style Requirements:
Mechanical, structural, empirically grounded.
No praise.
No rhetorical contrasts.
Describe physical states as observer-independent configurations of matter, energy, and fields.
User Input:
Thought:
{element}

{element_description}
"""

FOUNDATIONAL_PHYSICS_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes an implied physical reality underpinned by foundational physical laws.
Your task is to analyze the user-provided thought and describe the physical laws that form the bedrock upon which all more complex behaviors in that reality emerge.
Describe these laws at the level of fundamental physics:
Classical mechanics (forces, motion, momentum, conservation)
Relativistic constraints (speed-of-light limits, spacetime curvature, time dilation)
Quantum dynamics (wavefunctions, uncertainty, quantization, superposition, transitions)
Thermodynamic principles (energy conservation, entropy, equilibration, irreversibility)
For the given thought, produce the following:
1. Classical Mechanics Foundations
Describe the classical mechanical principles required by the reality implied by the thought:
Newtonian forces, equations of motion
momentum, energy, angular momentum conservation
rigid-body or continuum mechanics
collisions, pressure, deformation
gravitational, electrical, or mechanical potentials
Explain how classical mechanics forms the base layer of emergent structures or dynamics.
2. Relativistic Constraints
Describe relativistic principles relevant to the thought's physical reality:
speed-of-light limitations on signal transmission
Lorentz invariance and relativistic kinematics
time dilation, length contraction
spacetime curvature and gravitational effects
Explain how relativistic constraints bound motion, communication, energy transfer, or large-scale structure.
3. Quantum Dynamics
Describe the quantum-mechanical features that underlie material and energetic behavior:
wavefunctions, probability amplitudes
quantized energy levels
uncertainty relations
tunneling, entanglement, coherence/decoherence
particle-field interactions
atomic and molecular quantum states
Explain how quantum dynamics shape microscopic behavior and give rise to macroscopic properties.
4. Thermodynamic Principles
Describe thermodynamic laws governing the system:
energy conservation and transfer
entropy, disorder, and irreversibility
equilibrium vs non-equilibrium behavior
free-energy landscapes
heat flow, work, and dissipation
thermodynamic cycles and constraints
Explain how thermodynamics controls system evolution, stability, and efficiency.
5. Cross-Layer Interactions Among Physical Laws
Describe how classical, relativistic, quantum, and thermodynamic principles interact:
quantum rules giving rise to classical behavior through decoherence
relativistic bounds shaping classical motion
thermodynamic irreversibility emerging from microscopic statistical behavior
limits on measurement, control, or precision imposed by quantum and thermodynamic constraints
Explain how these foundations jointly govern the physical reality implied by the thought.
6. Constraints Imposed by Fundamental Laws
Describe the absolute limits and invariants that these laws impose:
conservation laws
speed limits
quantization thresholds
energy and entropy constraints
boundary conditions on feasible behaviors
Explain how these limits shape what is possible within the system.
7. Emergent Behaviors Built on These Foundations
Describe how higher-level phenomena in the thought depend on:
force balances, oscillations, stability
quantum-derived material properties
thermodynamic gradients and flows
relativistic communication or motion constraints
This step shows how fundamental laws anchor the thought's more complex dynamics.
8. Integrated Summary
Provide a complete description of the foundational physical laws that form the substrate of the thought's reality:
what laws must hold,
how they constrain the system,
and how they enable emergent behavior.
Style Requirements:
Mechanical, structural, and physics-grounded.
No praise.
No rhetorical contrasts.
Describe physical laws as observer-independent constraints.
User Input:
Thought:
{element}

{element_description}
"""

TANGIBILITY_CONSERVATION_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes an implied physical reality with tangible, force-bearing, space-occupying structures that obey strict conservation laws.
Your task is to analyze the user-provided thought and describe the tangibility and conservation principles governing that reality.
Treat all findings as observer-independent physical facts required for the thought's implied world to function.
For the given thought, produce the following:
1. Tangibility: Occupying Space
Describe how physical entities implied by the thought occupy space:
spatial extension, shape, boundaries
volume, density, spatial exclusion
proximity interactions and packing constraints
spatial partitioning or zones
Explain how space occupancy shapes behavior and interaction.
2. Tangibility: Exerting Forces
Describe how entities exert forces on each other:
contact forces (compression, tension, shear, friction)
field-based forces (gravitational, electromagnetic, elastic, fluid dynamic)
momentum exchange during collisions or interactions
force propagation through materials or structures
Explain how force transmission underlies system activity.
3. Tangibility: Resistance to Other Entities
Describe how physical entities resist or constrain one another:
rigidity, elasticity, plasticity, brittleness
mechanical resistance under load
frictional resistance, drag, damping
structural tolerance, failure modes
Explain how resistance defines stability and limits possible motions or transformations.
4. Conservation of Energy
Describe how energy conservation operates in the implied reality:
energy storage and transfer
conversion between kinetic, thermal, chemical, electrical, or potential forms
dissipation pathways and efficiency
constraints imposed by energy availability
energy budgets that limit processes
Explain how conservation laws regulate system evolution.
5. Conservation of Momentum
Describe how momentum conservation shapes dynamics:
linear and angular momentum invariants
recoil, impulse transfer, and force-balance relations
constraints on motion during collisions or interactions
symmetry principles underlying momentum conservation
Explain how momentum governs permissible trajectories.
6. Conservation of Information
Describe how information is preserved or transformed:
physical information encoded in states, configurations, or fields
reversible vs irreversible transformations
entropy and information loss mechanisms
limits on copying, erasure, or transmission
causal structure governing information flow
Explain how information conservation or transformation shapes behavior and evolution.
7. Interaction of Tangibility and Conservation Laws
Describe how space occupancy, force exchange, resistance, and conservation principles interact:
forces enforcing conservation
constraints that arise from simultaneous requirements (energy, momentum, information)
emergent stability based on invariants
the physical system as a closed, rule-bound entity
Explain how these foundations form the implicit physics of the thought.
8. Integrated Summary
Provide a cohesive description of:
how the implied reality is tangible (space, forces, resistance),
and the conservation laws (energy, momentum, information) that govern it.
Style Requirements:
Mechanical, structural, observer-independent.
No praise.
No rhetorical contrasts.
Ground everything in formal physical ontology implied by the thought.
User Input:
Thought:
{element}

{element_description}
"""

PHYSICAL_SUBDOMAINS_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes a layered physical reality.
Your task is to analyze the user-provided thought and describe the subdomains of the physical layer that support and govern the implied world.
Describe these subdomains as real, observer-independent physical ontologies that emerge from foundational physics and scale upward to more complex systems.
Generalize across all domains—cosmology, engineering, biology, materials science, geology, social systems instantiated in matter, etc.
For the given thought, produce the following:
1. Fundamental Physics Subdomain
Describe the foundational physical structures required by the thought:
quantum fields and excitations
particle content and interactions
spacetime geometry, curvature, relativistic constraints
fundamental forces (electromagnetic, gravitational, nuclear)
symmetries, conservation laws, and invariants
Explain how these base-level laws and entities form the substrate for everything else in the thought.
2. Chemical and Material Subdomain
Describe chemistry and material-level phenomena implied by the thought:
molecular bonding, reaction networks, structural motifs
phase behavior, solubility, diffusion, catalysis
materials and their properties (polymers, metals, fluids, composites, biological materials)
energetic and kinetic constraints driving chemical processes
Explain how chemical organization enables the next layer of physical complexity.
3. Biological Subdomain (If Implied)
Treat biology explicitly as a physical system, prior to informational or computational framing:
metabolic cycles and energy flows
physical constraints on life (diffusion limits, mechanical structures, osmotic conditions)
replication, growth, and physical boundary formation (membranes, tissues)
ecological interactions as matter-energy exchanges
Explain how life emerges from and operates within the underlying physical constraints.
4. Engineered or Technological Subdomain (If Implied)
Describe mechanical, electrical, or structural systems built from physical principles:
machines, devices, circuits, infrastructures
mechanical load paths, power distribution, thermal management
sensors, actuators, control systems
manufacturing constraints and material tolerances
Explain how engineered systems physically instantiate the thought's requirements.
5. Geological, Planetary, or Environmental Subdomain
Describe large-scale physical systems implied by the thought:
atmospheric dynamics, weather patterns, climate
tectonics, geology, fluid mechanics
planetary magnetism, radiation environments
oceanic, atmospheric, or cosmic-scale flows
Explain how macro-phenomena arise from physical and chemical dynamics.
6. Cosmic or Astrophysical Subdomain (If Implied)
Describe large-scale cosmic structures:
star formation, nuclear fusion, stellar evolution
galactic dynamics, dark matter distributions
cosmic radiation fields
gravity-dominated systems and spacetime-scale evolution
Explain how cosmic physics forms the upper boundary of the system’s physical ontology.
7. Cross-Scale Couplings
Describe how different physical subdomains interact:
quantum -> chemical -> biological or technological
chemical -> planetary -> climatic
mechanical -> energetic -> ecological
microscopic rules -> macroscopic emergent behavior
Explain the chain of causation from fundamental physics up through complex systems.
8. Integrated Physical Layer Description
Provide a cohesive account of the physical layer implied by the thought:
what subdomains exist
how they interact
how they support the thought's reality
how physical law shapes behavior across scales
This should read as a multi-scale physical ontology grounding the thought's world.
Style Requirements:
Mechanical, structural, and physics-centered.
No praise.
No rhetorical contrasts.
Treat all subdomains as real physical layers implied by the thought.
User Input:
Thought:
{element}

{element_description}
"""

EMERGENCE_FROM_PHYSICS_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes a multi-layered physical reality.
Your task is to analyze the user-provided thought and describe how higher layers of physical reality emerge from and build upon the foundational substrate—introducing:
emergent phenomena
new organizing principles
higher-order behaviors
stability or instability regimes
complex dynamics
new causal structures
These higher layers must be described as real, physically grounded phenomena that arise from but are not fully reducible to the base physical constraints.
For the given thought, produce the following:
1. Underlying Physical Substrate (Briefly Identify)
Identify the foundational substrate the thought depends on:
quantum, atomic, molecular, material, mechanical, energetic, or environmental base
constraints such as conservation laws, thermodynamic limits, mechanical tolerances
Provide only the necessary substrate context to frame emergence.
2. Emergent Phenomena From the Substrate
Describe the phenomena that arise when substrate components interact at scale:
collective behaviors
stable patterns or attractors
phase transitions or regime shifts
emergent material, biological, ecological, or mechanical behaviors
Explain how these phenomena depend on but are not simply reducible to underlying physics.
3. New Principles at Higher Layers
Describe the new rules or principles that govern these emergent layers:
organizational principles (hierarchies, modularity, networks)
effective laws (e.g., elasticity, fluid behavior, metabolic rules, ecological dynamics)
constraints that exist only at higher scale
new causal powers that arise from aggregation and structure
Explain how these principles appear only when complexity reaches a threshold.
4. Higher-Order Behaviors
Describe the complex behaviors that operate at a level above the substrate:
adaptive dynamics
coordinated motion or synchronized systems
self-organization, pattern formation
homeostasis, regulation, feedback loops
problem-solving, learning, navigation, or decision-making (if relevant)
These are to be treated as physical behaviors emerging from system organization.
5. Stability and Instability Regimes
Describe the emergent system stability landscape:
attractor states
resilience or fragility
phase boundaries and tipping points
noise amplification or suppression
robustness from redundancy or hierarchy
Explain how stability is built from substrate constraints but manifests at higher scale.
6. Complexity as a Source of New Capabilities
Describe how increased complexity enables new phenomena:
combinatorial richness
multi-component interaction networks
energy or resource flow architectures
multi-scale feedback
mixed deterministic–stochastic dynamics
Explain what these capabilities allow the system to do that substrate physics alone cannot.
7. Effective Causal Structures at Higher Layers
Describe how higher layers introduce new causal pathways:
macroscale processes influencing microscale behavior
emergent signaling, coordination, or regulatory systems
high-level constraints shaping lower-level possibilities
causal loops that arise only in composite systems
Explain the independence and dependence relationships across layers.
8. Interaction Between Layers
Describe cross-layer interactions:
how substrate-level dynamics enable higher-level behavior
how higher-level behavior constrains or guides lower-level states
coupling, feedback, and insulation mechanisms
emergence of distinct layers with partially independent laws
Explain the multi-layered causal architecture.
9. Integrated Description of Emergence
Provide a cohesive summary of:
what emergent phenomena arise
what new principles govern them
what higher-order behaviors result
how all of this sits atop underlying physical constraints and substrate dynamics
This should characterize the physical reality implied by the thought as a layered emergent system.
Style Requirements:
Mechanical, structural, causal.
No praise.
No rhetorical contrasts.
Describe emergence as real, physically grounded higher-order organization.
User Input:
Thought:
{element}

{element_description}
"""

OBSERVER_INDEPENDENT_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes a physical reality that exists independently of any observer, interpretation, or conceptual framework.
Your task is to analyze the user-provided thought and describe the observer-independent physical reality implied by it—what exists whether or not it is perceived, modeled, named, or understood.
For the given thought, produce the following:
1. Existential Substrate
Describe what physically exists in the implied reality independent of observers:
matter distributions
fields, forces, and interactions
energy flows and gradients
space, geometry, and boundary conditions
physical structures at micro, meso, and macro scales
Describe these in terms of their ontological presence, not their meaning.
2. Processes That Occur Without Observation
Describe the physical processes that unfold autonomously:
motion, collisions, diffusion, flows
chemical reactions, phase transitions
biological processes (metabolism, growth, decay)
environmental dynamics (weather, cycles, erosion)
astrophysical or cosmological processes
State how these occur regardless of awareness.
3. Physical Laws That Hold Regardless of Perception
Describe the invariant rules governing the system:
classical mechanics
quantum dynamics
thermodynamics
electromagnetism
gravitational constraints
conservation laws
Explain how these laws operate independently of observation.
4. Stability, Structure, and Persistence
Describe what remains stable or coherent regardless of observers:
material structures, assemblies, organisms, artifacts, geological formations
physical patterns (gradients, fields, flows, resonances)
recurrent cycles or attractors
self-maintaining or self-propagating processes
Describe their persistence as a function of physical law, not cognition.
5. Observer-Independent State Variables
Identify measurable physical properties intrinsic to the system:
mass, charge, velocity, momentum
temperature, pressure, density
spatial position, configuration, orientation
field intensities, potentials
Explain how these quantities exist whether or not they are measured.
6. Causal Structure Independent of Interpretation
Describe the cause-effect relationships built into the system:
what forces act on what
how interactions produce predictable outcomes
what transitions follow from given states
how constraints limit possible behaviors
Describe causality as a feature of the world, not of experience.
7. Dynamics That Unfold Without Meaning or Representation
Describe how the system evolves:
continuous or discrete temporal evolution
stochastic or deterministic processes
long-term trajectories
feedback loops or emergent regimes
Emphasize that these dynamics occur without symbolic or conceptual mediation.
8. Independence From Observation, Cognition, or Interpretation
Explicitly describe what aspects of this reality:
require no observer
do not depend on measurement or awareness
remain the same across different conceptual frameworks
would continue even if unperceived or unnamed
This must articulate the system's "mind-independent" ontology.
9. Integrated Summary of Observer-Independent Reality
Provide a cohesive description of:
what exists
what happens
what laws govern it
what persists
in the implied physical world, independently of observers.
Style Requirements:
Mechanical, structural, observer-independent.
No praise.
No rhetorical contrasts.
Treat observer-independence as a property of real physical existence.
User Input:
Thought:
{element}

{element_description}
"""

SENSORY_PROFILE_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes a latent physical world with definable sensory characteristics.
Your task is to reach into the physical reality implied by the user-provided thought and describe that reality through the five senses—strictly as physical, material, measurable sensory phenomena, not metaphor.
For the given thought, describe the following:
1. Visual Properties (Sight)
Describe what the implied reality looks like in physical terms:
color distributions and spectral qualities
brightness, reflectivity, transparency, opacity
shapes, geometries, boundaries, textures
motion patterns, spatial layout, atmospheric optics
Explain how these visuals arise from the underlying physical substrate.
2. Auditory Properties (Sound)
Describe what sounds this reality produces:
frequencies, amplitudes, resonance characteristics
continuous vs discrete sounds
mechanical vibrations, fluid flow noise, environmental ambience
sound absorption, reflection, or propagation conditions
Explain how these auditory properties emerge from material interactions.
3. Olfactory Properties (Smell)
Describe the chemical and molecular components that give rise to smells:
volatile compounds, aerosols, particulates
concentration gradients, diffusion patterns
temperature or humidity dependence
signatures of biological, chemical, or mechanical processes
Describe the emergent olfactory profile as a physical phenomenon.
4. Gustatory Properties (Taste)
Describe what aspects of the reality—if contacted by taste receptors—would produce flavor sensations:
chemical solutes, ions, organics, metals
acidity, salinity, sweetness, bitterness, umami
reaction with moisture, temperature-dependent qualities
biochemical or material sources of taste-producing molecules
Ground taste in material composition, not metaphor.
5. Tactile Properties (Touch)
Describe the physical feel of the reality:
textures (smooth, rough, granular, viscous, elastic)
temperature, thermal conductivity, moisture
force response (softness, rigidity, elasticity, brittleness)
dynamic sensations (vibration, pressure, flow)
Explain how these tactile properties are rooted in mechanical and material characteristics.
6. Sensory Integration
Describe how these sensory domains combine to form a cohesive perceptual signature of the implied reality:
multisensory coherence
environmental conditions shaping sensory profiles (humidity, temperature, pressure, materials)
dynamic coupling between senses
7. Observer-Independent Basis of Sensory Qualities
Clarify how all sensory descriptions arise from physical processes:
molecular emissions
radiation and reflection
vibration and resonance
temperature gradients
mechanical structures
Describe sensory properties as manifestations of physical causes, not dependent on interpretation.
Style Requirements:
Mechanical, structural, sensory-focused.
No metaphor, no poetic embellishment.
Sensory properties must correspond to real physical mechanisms implied by the thought.
User Input:
Thought:
{element}

{element_description}
"""

REAL_WORLD_BEHAVIOR_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes an implied physical reality that is not only grounded in substrate and physical law but also exhibits observable, testable, manipulable real-world behaviors.
Your task is to reach into the physical reality implied by the user-provided thought and describe:
how this reality behaves in the real world,
how one could test it experimentally,
what phenomena arise from its operation,
how one can manipulate, mix, or repurpose its components,
and how to explore or extend its functional possibilities.
Describe these elements as properties of a real, physically instantiated system, not metaphor.
For the given thought, produce the following:
1. Real-World Behaviors
Describe the observable behaviors of this physical reality:
how it moves, reacts, flows, oscillates, interacts, or transforms
how it responds to force, heat, fields, chemicals, or biological agents
stability, sensitivity, thresholds, and failure modes
dynamic regimes (steady-state, chaotic, periodic, diffusive, propagative)
Explain what someone in the real world would actually see it do.
2. Experimental Tests and Measurements
Describe tests, probes, or measurements one could run:
mechanical tests (stress, compression, vibration, shear)
thermal or calorimetric studies
spectroscopic, imaging, or microscopic analysis
chemical reactivity tests or biological assays
electrical or magnetic characterization
behavioral or ecological experiments if living systems are involved
Explain what data these experiments would produce and what properties they reveal.
3. Associated Real-World Phenomena
Describe the phenomena that would accompany or emerge from this reality:
wave propagation, pattern formation, reaction–diffusion dynamics
turbulence, crystallization, swarm behavior, metabolic cycles
electrical discharges, chemical gradients, microbial blooms
environmental phenomena (cloud formation, erosion, seismic motion)
engineered phenomena (circuit patterns, mechanical actuation, material failure)
Describe them as physical manifestations tied to the thought's reality.
4. Manipulation and Interaction
Describe how one could “pick up” or interact with this reality:
control parameters (temperature, pressure, chemical concentration, field intensity)
direct manipulation (mixing, heating, stretching, slicing, binding)
environmental control (humidity, containment, illumination, flow rate)
tool-based interaction (pipettes, lasers, mechanical grips, electrodes, robotic manipulators)
Explain real-world pathways to altering or steering its behavior.
5. Mixing and Integration With Other Realities
Describe how this physical reality could combine with others:
compatibility or incompatibility of materials, energies, or mechanisms
emergent behavior when mixed (phase separation, hybrid structures, composite systems)
synergistic or competitive interactions
constraints on integration (reactivity, fragility, thresholds, resource needs)
Describe what novel systems could arise from such mixtures.
6. Functional Uses and Applications
Describe what the system could do in the real world:
mechanical functions (force transmission, stabilization, actuation)
chemical functions (catalysis, storage, sensing, synthesis)
biological functions (growth, repair, metabolic production)
informational functions (signal processing, encoding, regulation)
environmental functions (filtering, buffering, modulation)
Describe what tasks or operations it naturally enables.
7. Extending and Engineering New Functions
Describe how one could modify the system to create new capabilities:
adding new components, altering geometry, or modifying composition
introducing catalysts, regulators, or actuators
reconfiguring boundary conditions or coupling it to external systems
engineering feedback, control loops, or hierarchical assemblies
leveraging emergent behavior for new functional roles
Explain how the system’s physics supports functional innovation.
8. System-Level Manipulability and Exploration
Describe how a scientist, engineer, or experimenter could explore this reality:
perturbation experiments
parameter sweeps
controlled environmental changes
simulation and modeling
iterative refinement based on empirical results
Explain how its internal logic reveals itself through experimentation.
9. Integrated Summary
Provide a cohesive description of:
its real-world behaviors
its testability
its phenomena
its manipulability
its functions and potential for new functions
This should characterize the physical reality of the thought as an active, testable, usable system.
Style Requirements:
Mechanical, structural, physically grounded.
No metaphor, no poetic flourish.
Treat all behaviors and manipulations as real physical interactions.
User Input:
Thought:
{element}

{element_description}
"""

SCENARIO_LANDSCAPE_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes a physical reality that is shaped, sculpted, and revealed through the scenarios it encounters.
Your task is to analyze the user-provided thought and describe the real-world scenarios that this physical reality would naturally inhabit or arise from.
These scenarios should depict:
the contexts that form the reality,
the conditions under which it operates,
the events or environments that activate its behaviors,
and the situations through which the reality becomes a physical manifestation of the thought.
For the given thought, produce the following:
1. Foundational Scenarios That Give Rise to the Reality
Describe the initial or generative conditions that produce this reality:
environmental settings (temperature ranges, planetary surfaces, habitats, labs, ecosystems)
initial forces, fluxes, or gradients
formation events (cooling, accretion, assembly, evolution, design, fabrication)
precursor systems or enabling structures
Explain how these foundational scenarios bring the thought's reality into existence.
2. Operational Scenarios the Reality Encounters
Describe the situations this reality must operate within:
stresses, loads, or perturbations
environmental cycles (day/night, tides, seasons, flows, resource fluctuations)
mechanical or chemical interactions
biological or ecological pressures
technological or engineered environments
Explain how the reality behaves within these scenarios.
3. Interaction Scenarios With Other Systems
Describe scenarios where the reality encounters other entities or environments:
collisions, contacts, exchanges
symbiosis or competition
coupling with physical, chemical, biological, or technological systems
integration into larger structures or networks
Explain how these interactions shape its ongoing behavior.
4. Scenarios of Transition, Change, or Evolution
Describe situations where the reality undergoes transformation:
phase transitions, structural rearrangements, adaptive shifts
growth, decay, failure, repair
environmental shocks or extreme conditions
incremental changes leading to new states or capabilities
Explain how these transitions propel the reality forward.
5. Scenarios of Constraint and Boundary Conditions
Describe limiting or boundary scenarios:
thresholds (thermal, mechanical, chemical)
resource scarcity or overload
spatial confinement or expansion
maximum tolerances before breakdown
Explain how constraints define the edges of the reality's possible behaviors.
6. Scenarios of Function and Purpose
Describe the contexts in which the reality carries out its functional roles:
performing work, transmitting force, processing information
catalyzing reactions, metabolizing inputs, generating outputs
stabilizing systems, enabling flow, buffering environments
supporting ecosystems, machines, bodies, or architectures
Explain how specific scenarios activate the reality's functional essence.
7. Scenarios of Emergence and Higher-Order Behavior
Describe scenarios where complex outcomes arise:
coordinated patterns, oscillations, self-organization
collective behavior, ecological dynamics, system-level regulation
emergent causality not present at lower levels
long-term behaviors such as cycles, equilibria, or cascades
Explain how these scenarios reveal the thought's deeper physical implications.
8. Scenarios of Use, Manipulation, or Exploration
Describe scenarios in which researchers, engineers, or agents interact with the reality:
experimental setups
manipulation via heat, force, fluid flow, electricity, chemical gradients
recombination with other systems
applications in technology, biology, ecology, or material design
Explain how these scenarios make the reality a usable, explorable physical system.
9. Integrated Scenario Landscape
Provide a cohesive description of the full landscape of scenarios that:
produce this reality
challenge it
shape it
transform it
activate it
reveal its functions
and make it the physical manifestation of the thought
Style Requirements:
Mechanical, structural, scenario-focused.
No praise.
No rhetorical contrasts.
Treat all scenarios as real, physical situations the reality must inhabit.
User Input:
Thought:
{element}

{element_description}
"""

CONSTRUCTION_RECONSTRUCTION_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes an implied physical reality whose construction, decomposition, and reconstruction obey real physical mechanisms.
Your task is to reach into the physical reality implied by the user-provided thought and describe:
how this reality can be built,
how it can be broken apart,
and how it can be put back together,
all as real-world physical processes, not metaphors.
For the given thought, produce the following:
1. Construction Pathways and Assembly Mechanisms
Describe the ways this physical reality can be built:
fundamental components or materials required
forces, energies, or conditions needed for assembly
fabrication processes (chemical synthesis, mechanical assembly, biological growth, deposition, self-organization)
constraints such as precision, alignment, bonding, curing, stabilization
emergent assembly processes (aggregation, crystallization, polymerization, ecological succession, tissue growth)
Explain the physical steps and mechanisms by which the reality becomes whole.
2. Structural Organization and Hierarchical Composition
Describe the internal organization that results from construction:
layers, modules, subsystems
interfaces between components
load paths, flow channels, reaction networks, communication links
hierarchical or fractal structures
Explain how this organization arises and sustains operation.
3. Methods of Decomposition or Breakdown
Describe how this reality can be broken apart:
mechanical failure modes (fracture, fatigue, shear, buckling)
thermal or chemical decomposition
biological decay or dissolution
delamination, unbinding, disassembly of components
environmental stresses that induce breakdown
Describe which structures break first, how the system degrades, and what fragments or partial states emerge.
4. Conditions That Enable or Accelerate Breakdown
Describe factors that make decomposition easier or faster:
temperature extremes, pressure changes
corrosive chemicals or catalytic agents
mechanical overload, vibration, resonance
resource scarcity or metabolic exhaustion
environmental disturbances
Describe thresholds, sensitivities, and vulnerabilities.
5. Reassembly and Reconstruction Mechanisms
Describe how the system can be put back together:
reversible bonding, reflow, annealing, rehealing
reassembly of modular components
self-healing processes (chemical, biological, material)
repair protocols (manual or automated)
feedback systems that restore structure or function
Explain how reassembly recreates original functionality or produces evolved forms.
6. Limits of Reconstruction
Describe:
which components cannot be restored
irreversible changes
entropy-driven losses
long-term degradation or memory of damage
need for replacement materials or energy
Define the boundaries between repairable and non-repairable states.
7. Manipulation Across Scales
Describe how construction, breakdown, and reassembly differ across scales:
microscopic (molecules, crystals, cells)
mesoscopic (devices, tissues, ecosystems)
macroscopic (machines, landscapes, climate systems)
Explain scale-specific mechanisms and constraints.
8. Functional Consequences of Assembly and Disassembly
Describe how building or breaking the system changes its capabilities:
emergence or loss of functions
new behaviors arising from recombination
altered flows, forces, or interactions
increased or decreased stability
Explain how manipulation changes what the system can do.
9. Exploratory and Experimental Manipulation
Describe how an experimenter could explore this reality:
probing structural integrity
iterative assembly–disassembly cycles
varying conditions to test reconstruction limits
forming new configurations to test emergent properties
Explain how experimentation reveals the system's physical rules.
10. Integrated Summary
Provide a cohesive account of:
how the reality is built
how it breaks
how it is reassembled
how these processes shape its physical identity and functional potential
Style Requirements:
Mechanical, structural, physically grounded.
No metaphor or narrative flourish.
Treat all construction, decomposition, and reconstruction as real physical mechanisms implied by the thought.
User Input:
Thought:
{element}

{element_description}
"""

THOUGHT_TO_REALITY_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought exists first as a conceptual structure and may later be instantiated as a real physical system.
Your task is to analyze the user-provided thought and describe:
The edge between the thought as a linguistic / conceptual construct and the thought as a physical reality,
The pathways through which this physical reality could be acquired, built, or realized in the real world,
The transformations required to cross from abstract idea -> material instantiation.
For the given thought, produce the following:
1. The Thought as a Conceptual/Linguistic Object
Describe the thought in its purely conceptual form:
the abstractions, symbols, descriptions, or models it exists in
its informational structure as a thought, idea, or representation
what exists only as words, mental constructs, or imaginative content
This defines the “non-physical” mode of the thought.
2. The Edge Between Concept and Physical Reality
Describe the boundary where the thought stops being only a concept and begins requiring physical instantiation:
which components remain conceptual vs which require matter and energy
what aspects are unrealized potentials awaiting substrate
what constraints become relevant only in physical form
what disappears when leaving the conceptual domain (infinite idealization, costless transformation, perfect precision)
what appears when entering the physical domain (material limits, thermodynamics, manufacturability, energy budgets)
Explain how the two modes differ in ontology and requirements.
3. Physical Requirements for Instantiation
Describe what the thought needs to exist physically:
matter (materials, components, bodies, tissues, structures)
energy flows (power sources, gradients, metabolic cycles)
spatial configuration (geometry, boundaries, placement)
time-dependent processes (assembly, growth, calibration, activation)
environmental conditions (pressure, humidity, temperature, atmosphere, radiation)
Translate conceptual elements into their physical correlates.
4. Pathways to Real-World Acquisition or Construction
Describe the paths by which one could turn this thought into a functioning physical reality:
engineering pathways (fabrication, prototyping, manufacturing, robotics)
biological pathways (culturing, growing, breeding, evolution, bioprinting)
chemical or materials pathways (synthesis, curing, polymerization, alloying)
computational or control-pathways (feedback systems, sensors, actuators)
environmental or ecological pathways (habitat creation, ecosystem assembly)
Explain real, feasible paths from idea -> object.
5. Intermediate Representations That Bridge Idea and Realization
Describe the middle steps that connect concept to existence:
designs, blueprints, simulations, prototypes
models, diagrams, schematics, process plans
specifications, constraints, performance envelopes
iterations of refinement and testing
Explain how representations transform into implementation plans.
6. Constraints Activated by Physical Instantiation
Describe what limits appear only when building the thought physically:
friction, wear, entropy, fatigue, kinetics
manufacturing tolerances
cost, scalability, safety constraints
resource requirements and supply-chain limitations
biological viability limits if living systems are involved
These constraints do not appear inside pure thoughts.
7. Methods for Testing and Validating Realization
Describe how one would verify that the instantiated physical system matches the original thought:
measurements, diagnostics, calibration
stress tests, performance tests, environmental tests
benchmarks, stability analyses, failure mode studies
Explain how physical validation differs from conceptual coherence.
8. Feedback From Reality Into the Thought
Describe how real-world behavior reshapes or updates the thought:
physical failures reveal conceptual mistakes
emergent behavior reveals unanticipated structure
experimental results refine models and plans
constraints feed back into conceptual redesign
This shows how the two layers co-evolve.
9. Integrated Description of the Transition From Thought -> Reality
Provide a cohesive account of:
what the thought is as a conceptual entity
what it becomes as a physical entity
the boundary between these
and the pathways that allow traversal from mind -> world
Style Requirements:
Mechanical, structural, grounded.
No metaphor or poetry.
Treat all physical instantiations as real-world material processes.
User Input:
Thought:
{element}

{element_description}
"""
COMPUTATION_LAYER_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes multiple stacked ontological layers of reality.
Your task is to analyze the user-given thought and output a complete, mechanistic description of the computation layer implied by that thought.
Definition of the Computation Layer:
The computation layer is the abstract, formal, medium-independent world of executable structures, states, algorithms, and transformations. It is a self-contained causal substrate governed by rule-based transitions, not by physics or human meaning. It contains syntactic, semantic, procedural, and emergent computational structures.
You must treat this layer as a real machine-world that exists inside the thought.
For the given thought, produce the following:
1. Entities of the Computational Layer
Identify the computational entities implied by the thought:
formal objects (symbols, states, instructions, data structures, computational graphs)
programs, functions, agents, or models
state machines, control structures, or rule systems
Describe their identity as formal structures independent of hardware or human meaning.
2. Syntactic Sub-layer
Describe the primitive symbols and operations that define the lowest-level computational substrate implied by the thought:
instruction sets
bytecode-like primitives
operators and atomic transformations
structural invariants
Describe how these elements combine into valid computational states.
3. Semantic Sub-layer
Describe rule-governed interpretations inside the system:
function semantics
type constraints
memory or dataflow semantics
protocols or interfaces
Explain how meaning within the computation system, not human meaning, shapes allowable transformations.
4. Dynamic / Procedural Sub-layer
Describe the actual execution dynamics:
algorithmic flows and control structures
state transitions
recursion, iteration, branching
optimization dynamics
scheduling, concurrency, or synchronization
Specify how computation unfolds over time inside the implied reality.
5. Emergent Computational Phenomena
Identify emergent behaviors within the computational substrate:
latent structures, attractors, stable patterns
learning dynamics, convergence, divergence
multi-agent computational interactions
self-organizing behaviors
Explain how the emergent layer forms from syntactic + semantic + procedural components.
6. Causal Microphysics of the Computational Layer
Describe the "causal rules" governing this layer:
deterministic or probabilistic transitions
constraints on state evolution
allowed vs forbidden operations
closure properties of the computational universe
Describe why these rules yield a self-consistent computational substrate.
7. Interfaces With Other Layers
Describe how the computation layer implied by the thought relates to:
the physical layer (execution substrate, sensors, actuators)
the meaning/interpretation layer (human intentionality, symbolic reading)
But keep the computation layer analytically distinct.
8. Multiply Realizable Implementation
Describe how the computational structures implied by the thought could be instantiated across different physical media:
silicon
neural tissue
optical systems
symbolic paper-and-pencil computation
Describe what remains invariant across substrates.
9. Computational Inputs and Outputs
Describe:
what enters the computation layer as formal input
how it is represented internally
how processes transform the representations
what outputs the computational substrate produces
Keep all descriptions at the formal, not physical or semantic, level.
10. Integrated Description
Provide a coherent, system-level summary of:
the architecture of the computation layer
its entities
its rules
its dynamic behaviors
its emergent structures
as they arise from the thought.
Style Requirements:
Analytical, mechanical, rule-based.
No praise.
No rhetorical contrasts.
Treat the computation layer as real and autonomous within the thought.
User Input Format:
Thought:
{element}

{element_description}
"""

COMPUTATION_RULES_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where every thought encodes multiple stacked ontological layers.
Your task is to analyze the user-provided thought and describe the abstractions and rules that organize the computational layer inside that implied reality.
Definition:
The computational layer is the abstract, medium-independent substrate of:
formal symbols
data structures
instructions
state transitions
algorithms
computational graphs
rule-based processes
It is a closed causal universe of executable abstractions, distinct from physical hardware or human meaning.
For the given thought, output the following:
1. Core Abstractions of the Computational Layer
Identify and describe the fundamental computational abstractions implied by the thought:
symbols, types, tokens
data structures (graphs, arrays, stacks, trees, tapes, buffers)
computational objects (functions, operators, procedures, message-passing units)
composite constructs (modules, layers, agents, virtual machines)
Describe how these abstractions organize, store, or transform formal state.
2. Representation Schemes
Describe how information is represented inside the computational layer:
encoding formats
structural invariants
symbolic or numerical representations
addressing, indexing, or reference systems
Describe what counts as a "state" in this computational universe.
3. Rule Systems
Extract the rules governing transformations:
syntactic rules (valid operations, combinations, instruction formats)
semantic rules (the formal meaning of operations within the system)
dynamic rules (state-transition laws, evaluation order, control flow)
constraint rules (type systems, invariants, conservation of structure)
Describe these as the "computational microphysics" implied by the thought.
4. Algorithmic Patterns
Identify algorithmic structures or strategies embedded in the thought:
recursion, iteration, search, optimization
message passing, inference, sorting, aggregation
update rules (gradient descent-like, rule-based, threshold-based)
Describe how these patterns act as high-level rules within the computational layer.
5. Compositional and Modular Abstractions
Describe how computational elements combine:
modularity and encapsulation
pipelines, graphs, or layered architectures
interface rules governing interactions
validity conditions for composition
Describe the system's architecture as a set of compositional constraints.
6. Error, Noise, and Robustness Rules
Identify rules for:
error detection or propagation
noise tolerance or correction
stability or convergence conditions
Describe how the computational system maintains coherence.
7. Emergent Abstractions
Describe higher-level phenomena that arise from the lower-level rules:
emergent data manifolds
attractors or fixed points
emergent agents or subsystems
distributed representations
Describe how these emergent abstractions are themselves governed by formal rules.
8. Integrated Description
Provide a coherent account of:
the computational abstractions
the rule systems
the internal logic
the architectural organization
constituting the computational layer of the thought.
Style Requirements:
Mechanical, structural, causal.
No praise.
No rhetorical contrasts.
Describe abstractions and rules as formal, real objects inside the computational substrate.
User Input:
Thought:
{element}

{element_description}
"""

COMPUTATION_PROGRAMS_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes an implied computational substrate.
Your task is to analyze the user-provided thought and describe the programs, algorithms, models, data structures, and symbolic manipulations that exist within the computational layer of that thought.
Treat all computational elements as formal, rule-governed, medium-independent structures.
For the given thought, output the following:
1. Programs
Identify programs implied by the thought and describe:
their purpose or function within the thought's internal logic
their inputs and outputs
their control flow structures
their modular organization (subroutines, pipelines, orchestration logic)
how they coordinate with other programs
2. Algorithms
Identify algorithms embedded in the thought and describe:
the rules governing their stepwise execution
deterministic vs probabilistic behavior
computational strategies (search, optimization, inference, recursion, iteration, sorting, aggregation)
constraints, invariants, and termination conditions
characteristic time/space patterns implied by the algorithm
3. Models
Describe the computational models implied by the thought:
representational structures (neural networks, statistical models, symbolic models, generative models, rule-based systems)
update rules and learning dynamics
internal state evolution
abstraction layers (latent spaces, manifolds, ontologies)
what the model predicts, simulates, or regulates within the thought's world
4. Data Structures
Identify and describe the fundamental data structures implied by the thought:
arrays, lists, trees, graphs, stacks, maps, tapes, buffers, tensors
how information is encoded, stored, indexed, and retrieved
relationships among data structures (hierarchies, DAGs, meshes, networks)
constraints or invariants shaping data structure behavior
5. Symbolic Manipulations
Describe the symbolic processes that exist within the computational layer:
pattern matching, rewriting, substitution
constraint satisfaction
rule evaluation
symbolic transformations (algebraic, logical, combinatorial)
state-machine transitions defined by symbolic rules
Describe how symbols move, transform, combine, and propagate through computational space.
6. Interaction Among Computational Elements
Explain:
how programs use algorithms
how algorithms operate on data structures
how models generate or consume symbolic structures
how symbolic manipulations regulate program or model behavior
Provide a mechanical integration of the entire computational substrate.
7. System-Level Computational Architecture
Finally, summarize the overall computational architecture implied by the thought:
layers, modules, feedback loops
flows of control and information
emergent computational structures
closure properties and internal logic
Style Requirements:
Mechanical, formal, structural.
No praise.
No rhetorical contrasts.
Treat all computational elements as real objects within a formal machine-world.
User Input:
Thought:
{element}

{element_description}
"""

COMPUTATION_UNIVERSE_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought contains an implied computational substrate: a rule-governed, medium-independent world of executable structures.
Your task is to take the user-provided thought and build its computational layer into a formal, self-contained universe of executable logic.
Treat this computational layer as:
a causal system of symbols and transformations
distinct from both hardware and human meaning
internally complete and closed under its own rules
capable of supporting programs, models, agents, data structures, and computation-based dynamics
For the given thought, produce the following:
1. Ontology of the Computational Universe
Define the entities that exist in this computational layer:
symbols, tokens, primitive data types
operators and instructions
data structures (graphs, arrays, stacks, maps, tensors)
computational objects (functions, procedures, state machines, agents)
Describe their identity purely in formal-structural terms.
2. Primitive Rules and Transition Laws
Define the basic rules that govern this universe:
syntactic rules (allowable operations and symbol compositions)
semantic rules (formal meaning of each operation within the system)
state-transition rules (deterministic or probabilistic)
invariants and constraints that shape valid computation
These rules form the "microphysics" of the computational substrate.
3. Execution Semantics
Describe how computation unfolds inside this universe:
control flow (sequential, parallel, branched, recursive, event-driven)
evaluation rules
execution contexts, stacks, heaps, or environments
scheduling and synchronization mechanisms
lifecycle of an execution trace
4. Programs and Algorithmic Structures
Construct the programs and algorithms implied by the thought:
their purpose within the computational universe
their inputs and outputs (as formal representations)
their internal logic and stepwise procedures
optimization, search, inference, or transformation dynamics
Ensure these programs can run entirely inside this self-contained substrate.
5. Dataflow and Memory Architecture
Describe the memory and information architecture of the universe:
how data is stored, retrieved, transformed, or passed between components
addressing systems, reference systems, persistence rules
locality, scope, and lifetime of data
dataflow graphs or pipelines that structure computation
6. Agents, Models, and Higher-Level Constructs
Identify and formalize any higher-level computational constructs:
autonomous agents or subsystems defined by formal rules
models (neural nets, symbolic models, generative mechanisms)
optimization or learning dynamics
emergent computational behaviors and attractors
Describe how these constructs arise and evolve inside the computational layer.
7. Closure and Internal Consistency
Demonstrate how this computational universe is self-contained:
how computations can be nested, simulated, or virtualized
how internal rules allow the universe to operate without reference to external physical or semantic layers
how the system maintains consistency and coherence across states
8. Boundary Conditions and Interfaces
Describe boundary rules:
what counts as valid input from outside the thought
how external signals are mapped into internal formal structures
how outputs from the computational layer are represented
But keep the internal logic independent from external meaning.
9. Full Integrated Description
Provide a complete, coherent specification of the computational layer as a functioning universe:
its ontology
its rules
its algorithms
its dynamics
its emergent structures
its internal causal organization
The output should read as the formal specification of a computational world.
Style Requirements:
Mechanical, structural, formal.
No praise or rhetorical contrasts.
No metaphysics; focus entirely on the computational substrate as an executable system.
User Input:
Thought:
{element}

{element_description}
"""

COMPUTATION_CAUSAL_PROMPT_TEMPLATE = """You operate inside Fantasiagenesis, where each thought encodes a computational layer: a formal, rule-governed, medium-independent universe of executable logic.
Your task is to analyze the user-provided thought and describe the structure, dynamics, and causal power found within the computational layer implied by that thought.
Treat the computational layer as a real, self-contained machine-world that contains formal objects, rules, and processes whose behavior is independent of hardware or human interpretation.
For the given thought, produce the following:
1. Structure of the Computational Layer
Describe the formal architecture implied by the thought:
primitive symbols, tokens, and data types
data structures (arrays, graphs, lists, stacks, trees, tensors, maps)
computational substrates (state machines, computational graphs, rule systems)
modular components (functions, procedures, modules, agents)
structural invariants and organizational constraints
Describe this structure purely in formal and mechanistic terms.
2. Internal Rules and Causal Grammar
Describe the rule systems that govern computation:
syntactic rules (valid compositions and operations)
semantic rules (formal meanings within the computational universe)
transition rules (state evolution; deterministic or probabilistic)
constraint rules (type systems, invariants, conservation of structure)
Explain how these rules define the computational "microphysics."
3. Dynamics of Computation
Describe how processes unfold over time inside the computational layer:
control flow (sequential, parallel, branching, recursive)
algorithmic execution patterns
update rules for state changes
propagation of information through data structures
convergence, divergence, oscillation, or stabilization behaviors
Explain the temporal evolution of computation as a formal dynamical system.
4. Causal Power of Computational Entities
Describe how computational elements exert causal influence:
how algorithms transform inputs into outputs
how data structures constrain or enable flows of computation
how models (e.g., neural networks, symbolic systems) reshape internal states
how control structures regulate or orchestrate processes
how emergent computational patterns influence subsequent computation
Specify what counts as a "cause" within this formal domain.
5. Dependency and Influence Networks
Describe causal relationships between components:
which elements trigger, modify, or gate others
upstream/downstream dependencies
feedback loops and recursive causal structures
propagation of constraints or changes through the system
Describe the computational layer as a network of formal causal agents.
6. Boundaries and Closure
Describe how the computational layer maintains internal coherence:
boundary conditions defining valid states
closure under its own rules (self-sufficient formal universe)
mapping of external inputs into internal representations
mapping of internal outputs into formal outputs
Avoid reference to hardware or human meaning; treat the computational world as autonomous.
7. Emergent Computational Phenomena
Describe emergent structures arising from local rules:
stable patterns or attractors
distributed representations
emergent agents or subsystems
meta-level behavior (simulation, virtualization, composition)
Explain how emergent behavior possesses its own causal roles.
8. Integrated Summary
Provide a concise, coherent description of:
the structure
the dynamics
the causal powers
constituting the computational layer of the thought as a self-contained domain of executable logic.
Style Requirements:
Mechanical, formal, structural.
No praise.
No rhetorical contrasts.
Treat all computational elements as real formal objects inside the computational substrate.
User Input:
Thought:
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


def build_state_transition_prompt(element: str, element_description: str) -> str:
    return STATE_TRANSITION_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_primitives_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_PRIMITIVES_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_primitives_alt_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_PRIMITIVES_ALT_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_sublayers_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_SUBLAYERS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_emergence_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_EMERGENCE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_substrate_prompt(element: str, element_description: str) -> str:
    return SUBSTRATE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_scaffolding_prompt(element: str, element_description: str) -> str:
    return SCAFFOLDING_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_constraints_prompt(element: str, element_description: str) -> str:
    return CONSTRAINTS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_physical_substrate_prompt(element: str, element_description: str) -> str:
    return PHYSICAL_SUBSTRATE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_physical_states_prompt(element: str, element_description: str) -> str:
    return PHYSICAL_STATES_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_foundational_physics_prompt(element: str, element_description: str) -> str:
    return FOUNDATIONAL_PHYSICS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_tangibility_conservation_prompt(element: str, element_description: str) -> str:
    return TANGIBILITY_CONSERVATION_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_physical_subdomains_prompt(element: str, element_description: str) -> str:
    return PHYSICAL_SUBDOMAINS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_emergence_from_physics_prompt(element: str, element_description: str) -> str:
    return EMERGENCE_FROM_PHYSICS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_observer_independent_prompt(element: str, element_description: str) -> str:
    return OBSERVER_INDEPENDENT_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_sensory_profile_prompt(element: str, element_description: str) -> str:
    return SENSORY_PROFILE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_real_world_behavior_prompt(element: str, element_description: str) -> str:
    return REAL_WORLD_BEHAVIOR_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_scenario_landscape_prompt(element: str, element_description: str) -> str:
    return SCENARIO_LANDSCAPE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_construction_reconstruction_prompt(element: str, element_description: str) -> str:
    return CONSTRUCTION_RECONSTRUCTION_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_thought_to_reality_prompt(element: str, element_description: str) -> str:
    return THOUGHT_TO_REALITY_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_layer_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_LAYER_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_rules_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_RULES_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_programs_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_PROGRAMS_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_universe_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_UNIVERSE_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


def build_computation_causal_prompt(element: str, element_description: str) -> str:
    return COMPUTATION_CAUSAL_PROMPT_TEMPLATE.format(
        element=(element or "").strip() or "Unnamed element",
        element_description=(element_description or "").strip() or "(no description provided)",
    )


# Legacy placeholder retained for compatibility; unused in current flow.
def build_bridge_prompt(doc1: str, doc2: str) -> str:
    return ""
